from sqlmodel import Session, select, col
from sqlalchemy import func

from src.repositories.models import Tweet, TweetCreate, TweetRead
from src.types import Embedding

from .interfaces import TweetRepositoryInterface


class TweetRepository(TweetRepositoryInterface):
    def __init__(self, session: Session):
        self.session = session

    def add(self, tweet: TweetCreate) -> TweetRead:
        # Check if a tweet with the same tweet_id exists
        statement = select(Tweet).where(Tweet.tweet_id == tweet.tweet_id)
        existing_tweet = self.session.exec(statement).first()

        if existing_tweet:
            return TweetRead.model_validate(existing_tweet)

        # If no existing tweet found, create a new one
        db_tweet = Tweet.model_validate(tweet)
        self.session.add(db_tweet)
        self.session.flush()
        self.session.refresh(db_tweet)

        return TweetRead.model_validate(db_tweet)

    def get(self, id: int, thread_id: int) -> TweetRead | None:
        statement = (
            select(Tweet).where(Tweet.id == id).where(Tweet.thread_id == thread_id)
        )
        tweet = self.session.exec(statement).first()
        if not tweet:
            return None

        return TweetRead.model_validate(tweet)

    def get_by_id(self, id: int) -> TweetRead | None:
        tweet = self.session.get(Tweet, id)
        if not tweet:
            return None

        return TweetRead.model_validate(tweet)

    def get_by_tweet_id(self, tweet_id: str) -> TweetRead | None:
        statement = select(Tweet).where(Tweet.tweet_id == tweet_id)
        tweet = self.session.exec(statement).first()
        if not tweet:
            return None

        return TweetRead.model_validate(tweet)

    def get_random(self) -> TweetRead | None:
        statement = (
            select(Tweet)
            .where(Tweet.embedding_is_not_null())
            .order_by(func.random())
            .limit(1)
        )
        tweet = self.session.exec(statement).first()
        if not tweet:
            return None

        return TweetRead.model_validate(tweet)

    def get_by_thread_id(self, thread_id: int) -> list[TweetRead]:
        statement = (
            select(Tweet)
            .where(Tweet.thread_id == thread_id)
            .order_by(col(Tweet.position_in_thread))
        )
        tweets = self.session.exec(statement).all()
        return [TweetRead.model_validate(tweet) for tweet in tweets]

    def find_similar_tweets(
        self, tweet: TweetRead, limit: int = 5, similarity_threshold: float = 0.5
    ) -> list[TweetRead]:
        """
        Find tweets similar to the given tweet using vector similarity.
        Only searches within the same thread as the input tweet.

        Args:
            tweet: The tweet to find similar tweets for
            limit: Maximum number of similar tweets to return (default: 5)
            similarity_threshold: Maximum cosine distance to consider tweets similar (default: 0.5)
                                Lower values mean more similar (0 = identical, 1 = completely different)

        Returns:
            A list of similar tweets from the same thread, ordered by similarity (most similar first)
        """
        if tweet.embedding is None:
            return []

        distance = Tweet.embedding_cosine_distance(tweet.embedding)

        statement = (
            select(Tweet)
            .where(Tweet.id != tweet.id)
            .where(Tweet.thread_id == tweet.thread_id)
            .where(Tweet.embedding_is_not_null())
            .where(distance <= similarity_threshold)
            .order_by(distance)
            .limit(limit)
        )

        tweets = self.session.exec(statement)
        return [TweetRead.model_validate(t) for t in tweets]

    def search_tweets_by_embedding(
        self, embedding: Embedding, limit: int = 10, similarity_threshold: float = 0.5
    ) -> list[TweetRead]:
        """
        Search for tweets similar to the given embedding across all threads.

        Args:
            embedding: The embedding vector to search for
            limit: Maximum number of tweets to return (default: 10)
            similarity_threshold: Maximum cosine distance to consider tweets similar (default: 0.5)
                                Lower values mean more similar (0 = identical, 1 = completely different)

        Returns:
            A list of similar tweets from all threads, ordered by similarity (most similar first)
        """
        distance = Tweet.embedding_cosine_distance(embedding)

        statement = (
            select(Tweet)
            .where(Tweet.embedding_is_not_null())
            .where(distance <= similarity_threshold)
            .order_by(distance)
            .limit(limit)
        )

        tweets = self.session.exec(statement)
        return [TweetRead.model_validate(t) for t in tweets]

    def get_tweet_counts_by_thread_ids(self, thread_ids: list[int]) -> dict[int, int]:
        """
        Get the count of tweets for each thread ID in the given list.

        Args:
            thread_ids: List of thread IDs to get tweet counts for

        Returns:
            Dictionary mapping thread_id to tweet count. Threads with no tweets won't appear in the result.
        """
        if not thread_ids:
            return {}

        thread_id_col = col(Tweet.thread_id)

        statement = (
            select(thread_id_col, func.count())
            .select_from(Tweet)
            .where(thread_id_col.in_(thread_ids))
            .group_by(thread_id_col)
        )

        results = self.session.exec(statement)
        return {thread_id: count for thread_id, count in results}

    def delete_by_thread_id(self, thread_id: int) -> None:
        statement = select(Tweet).where(Tweet.thread_id == thread_id)
        tweets = self.session.exec(statement).all()
        for tweet in tweets:
            self.session.delete(tweet)
        self.session.flush()

    def count_with_embeddings(self) -> int:
        """
        Count tweets that have embeddings.

        Used for weighted random selection to ensure proportional distribution
        between notes, URL chunks, and tweets in the unified /random endpoint.

        Returns:
            Number of tweets with non-null embeddings
        """
        statement = (
            select(func.count()).select_from(Tweet).where(Tweet.embedding_is_not_null())
        )

        count = self.session.exec(statement).first()
        return count or 0
