from flask import Flask, render_template, request, jsonify, session, redirect
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import os, re, time
from werkzeug.utils import secure_filename
from PIL import Image
import pytesseract
import google.generativeai as genai

# -------------------------------
# Flask App Setup
# -------------------------------
app = Flask(__name__)
app.secret_key = "super_secret_ai_expense_key_v2"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///app.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = "uploads"

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
db = SQLAlchemy(app)

# -------------------------------
# Gemini AI Setup
# -------------------------------
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY_HERE"
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel("gemini-1.5-flash")

# -------------------------------
# Database Models
# -------------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    account_no = db.Column(db.String(30), unique=True, nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    transactions = db.relationship("Transaction", backref="user", lazy=True)
    budgets = db.relationship("Budget", backref="user", lazy=True)
    alerts = db.relationship("Alert", backref="user", lazy=True)

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(80), default="Others")
    description = db.Column(db.String(200))
    sms_message = db.Column(db.Text)  # stores full pasted message
    date = db.Column(db.DateTime, default=datetime.utcnow)

class Budget(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    category = db.Column(db.String(80), nullable=False)
    monthly_limit = db.Column(db.Float, nullable=False)

class Alert(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    text = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# -------------------------------
# Utility Functions
# -------------------------------
def parse_sms_for_amount_category(text):
    """Extract amount and category from SMS"""
    amount = None
    match = re.search(r"Rs\.?\s?(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if match:
        amount = float(match.group(1))
    text_l = text.lower()
    if "flipkart" in text_l or "amazon" in text_l:
        category = "Shopping"
    elif "swiggy" in text_l or "zomato" in text_l:
        category = "Food"
    elif "uber" in text_l or "ola" in text_l or "travel" in text_l:
        category = "Travel"
    elif "bill" in text_l or "electricity" in text_l or "water" in text_l:
        category = "Bills"
    else:
        category = "Others"
    return amount, category

def check_budget_alerts(user_id):
    now = datetime.utcnow()
    start_month = datetime(now.year, now.month, 1)
    budgets = Budget.query.filter_by(user_id=user_id).all()
    for b in budgets:
        total = db.session.query(db.func.sum(Transaction.amount)).filter(
            Transaction.user_id == user_id,
            Transaction.category == b.category,
            Transaction.date >= start_month
        ).scalar() or 0
        if total > b.monthly_limit:
            alert_text = f"⚠️ You exceeded your {b.category} budget!"
            if not Alert.query.filter_by(user_id=user_id, text=alert_text).first():
                db.session.add(Alert(user_id=user_id, text=alert_text))
    db.session.commit()

# -------------------------------
# Auth Routes
# -------------------------------
@app.route("/")
def home():
    if "user_id" in session:
        user = User.query.get(session["user_id"])
        return render_template("index.html", username=user.username)
    return redirect("/login")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        data = request.form
        if User.query.filter(
            (User.username == data["username"]) |
            (User.account_no == data["account_no"]) |
            (User.email == data["email"])
        ).first():
            return render_template("register.html", error="⚠️ User already exists!")

        new_user = User(
            username=data["username"],
            account_no=data["account_no"],
            email=data["email"],
            password=data["password"]
        )
        db.session.add(new_user)
        db.session.commit()
        return redirect("/login")
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        user = User.query.filter_by(username=username, password=password).first()
        if user:
            session["user_id"] = user.id
            return redirect("/")
        return render_template("login.html", error="❌ Invalid username or password")
    return render_template("login.html")


# -------------------------------
# Transaction Routes
# -------------------------------
@app.route("/add_transaction", methods=["POST"])
def add_transaction():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Please login first"}), 401

    data = request.get_json()
    sms_text = data.get("sms", "")
    desc = data.get("description", "")

    if not sms_text.strip():
        return jsonify({"error": "Please paste the message"}), 400

    amount, category = parse_sms_for_amount_category(sms_text)
    if not amount:
        return jsonify({"error": "Could not detect amount in message"}), 400

    t = Transaction(
        user_id=user_id,
        amount=amount,
        category=category,
        description=desc,
        sms_message=sms_text
    )
    db.session.add(t)
    db.session.commit()
    check_budget_alerts(user_id)
    return jsonify({"success": True})

@app.route("/get_transactions")
def get_transactions():
    user_id = session.get("user_id")
    txs = Transaction.query.filter_by(user_id=user_id).order_by(Transaction.date.desc()).limit(50).all()
    return jsonify([{
        "date": t.date.strftime("%Y-%m-%d"),
        "amount": t.amount,
        "category": t.category,
        "description": t.description or "",
        "sms": t.sms_message
    } for t in txs])

# -------------------------------
# Budgets, Alerts, Dashboard
# -------------------------------
@app.route("/set_budget", methods=["POST"])
def set_budget():
    user_id = session.get("user_id")
    data = request.get_json()
    b = Budget.query.filter_by(user_id=user_id, category=data["category"]).first()
    if not b:
        b = Budget(user_id=user_id, category=data["category"], monthly_limit=data["amount"])
        db.session.add(b)
    else:
        b.monthly_limit = data["amount"]
    db.session.commit()
    return jsonify({"success": True})

@app.route("/get_budget")
def get_budget():
    user_id = session.get("user_id")
    budgets = Budget.query.filter_by(user_id=user_id).all()
    return jsonify({b.category: b.monthly_limit for b in budgets})

@app.route("/alerts", methods=["GET", "POST"])
def alerts():
    user_id = session.get("user_id")
    if request.method == "POST":
        Alert.query.filter_by(user_id=user_id).delete()
        db.session.commit()
        return jsonify({"cleared": True})
    alerts = Alert.query.filter_by(user_id=user_id).order_by(Alert.created_at.desc()).all()
    return jsonify([{"text": a.text, "created_at": a.created_at.strftime("%Y-%m-%d %H:%M")} for a in alerts])

@app.route("/dashboard_data")
def dashboard_data():
    user_id = session.get("user_id")
    since = datetime.utcnow() - timedelta(days=30)
    txs = Transaction.query.filter(Transaction.user_id == user_id, Transaction.date >= since).all()
    by_cat, by_date = {}, {}
    for t in txs:
        by_cat[t.category] = by_cat.get(t.category, 0) + t.amount
        d = t.date.strftime("%Y-%m-%d")
        by_date[d] = by_date.get(d, 0) + t.amount
    return jsonify({
        "by_category": by_cat,
        "daily": [{"date": k, "amount": v} for k, v in sorted(by_date.items())]
    })

# -------------------------------
# AI Features (Insights, Chatbot)
# -------------------------------
@app.route("/insights")
def insights():
    user_id = session.get("user_id")
    txs = Transaction.query.filter_by(user_id=user_id).all()
    text = "Analyze spending patterns:\n" + "\n".join([f"{t.category}: {t.amount}" for t in txs[-20:]])
    try:
        response = gemini_model.generate_content(text)
        insight = response.text.strip()
    except Exception as e:
        insight = f"AI Error: {e}"
    return jsonify({"insight": insight})

@app.route("/chatbot", methods=["POST"])
def chatbot():
    user_id = session.get("user_id")
    message = request.get_json().get("message", "")
    txs = Transaction.query.filter_by(user_id=user_id).all()
    summary = "\n".join([f"{t.category} - ₹{t.amount}" for t in txs[-10:]])
    prompt = f"Here is user's recent spending summary:\n{summary}\n\nUser asked: {message}"
    try:
        response = gemini_model.generate_content(prompt)
        reply = response.text.strip()
    except Exception as e:
        reply = f"AI Error: {e}"
    return jsonify({"response": reply})

# -------------------------------
# OCR Receipt
# -------------------------------
@app.route("/upload_receipt", methods=["POST"])
def upload_receipt():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"})
    file = request.files["file"]
    path = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(file.filename))
    file.save(path)
    try:
        text = pytesseract.image_to_string(Image.open(path))
    except Exception as e:
        return jsonify({"error": str(e)})
    return jsonify({"detection": text})

# -------------------------------
# Init DB
# -------------------------------
def init_db():
    with app.app_context():
        db.create_all()
        if not User.query.first():
            db.session.add(User(username="demo", account_no="1234567890", email="demo@test.com", password="demo"))
            db.session.commit()
        print("✅ Database initialized")

if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
