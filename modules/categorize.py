# modules/categorize.py
import pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
import joblib, os

def train_and_save(csv_path="data/labeled_transactions.csv", out="models/txn_clf.joblib"):
    df = pd.read_csv(csv_path)
    X = df['Description'].astype(str)
    y = df['Category'].astype(str)
    pipe = Pipeline([("tfidf", TfidfVectorizer(ngram_range=(1,2))), ("clf", MultinomialNB())])
    pipe.fit(X, y)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    joblib.dump(pipe, out)
    print("Saved model:", out)

if __name__ == "__main__":
    train_and_save()