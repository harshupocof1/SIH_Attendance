import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.svm import SVC
from sklearn.pipeline import Pipeline

class CampusChatbot:
    def __init__(self, dataset_path="chat_dataset.csv"):
        df = pd.read_csv(dataset_path)  # must have: user_input, bot_reply
        self.pipeline = Pipeline([
            ('tfidf', TfidfVectorizer()),
            ('clf', SVC())
        ])
        self.pipeline.fit(df['user_input'], df['bot_reply'])

    def get_reply(self, user_text: str) -> str:
        """Predicts the reply for a given user message."""
        return self.pipeline.predict([user_text])[0]
