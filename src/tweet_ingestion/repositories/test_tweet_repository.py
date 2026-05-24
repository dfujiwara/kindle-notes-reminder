"""
Tests for TweetRepository methods using in-memory database.
"""

import pytest
from datetime import datetime, timezone
from sqlmodel import Session

from .tweet_thread_repository import TweetThreadRepository
from .tweet_repository import TweetRepository
from src.repositories.models import TweetThreadCreate, TweetCreate, TweetRead


@pytest.fixture(name="sample_thread_id")
def sample_thread_id_fixture(tweet_thread_repo: TweetThreadRepository) -> int:
    """Create a sample tweet thread and return its ID."""
    thread = TweetThreadCreate(
        root_tweet_id="root123",
        author_username="testuser",
        author_display_name="Test User",
        title="Test Thread",
    )
    created_thread = tweet_thread_repo.add(thread)
    return created_thread.id


@pytest.fixture(name="sample_tweets")
def sample_tweets_fixture(
    tweet_repo: TweetRepository, sample_thread_id: int
) -> list[TweetRead]:
    """Create sample tweets and return them as TweetRead objects."""
    # Create a sample embedding (realistic production data)
    embedding = [0.1] * 1536
    tweeted_at = datetime.now(timezone.utc)

    tweets = [
        TweetCreate(
            tweet_id="tweet001",
            author_username="testuser",
            author_display_name="Test User",
            content="First tweet in thread",
            media_urls=[],
            thread_id=sample_thread_id,
            position_in_thread=0,
            tweeted_at=tweeted_at,
            embedding=embedding,
        ),
        TweetCreate(
            tweet_id="tweet002",
            author_username="testuser",
            author_display_name="Test User",
            content="Second tweet in thread",
            media_urls=["https://example.com/image.jpg"],
            thread_id=sample_thread_id,
            position_in_thread=1,
            tweeted_at=tweeted_at,
            embedding=embedding,
        ),
        TweetCreate(
            tweet_id="tweet003",
            author_username="testuser",
            author_display_name="Test User",
            content="Third tweet in thread",
            media_urls=[],
            thread_id=sample_thread_id,
            position_in_thread=2,
            tweeted_at=tweeted_at,
            embedding=embedding,
        ),
    ]
    added_tweets: list[TweetRead] = []
    for tweet in tweets:
        added_tweets.append(tweet_repo.add(tweet))

    return added_tweets


def test_add_new_tweet(tweet_repo: TweetRepository, sample_thread_id: int):
    """Test adding a new tweet."""
    tweeted_at = datetime.now(timezone.utc)
    new_tweet = TweetCreate(
        tweet_id="newtweet123",
        author_username="testuser",
        author_display_name="Test User",
        content="New test tweet",
        media_urls=["https://example.com/pic.png"],
        thread_id=sample_thread_id,
        position_in_thread=0,
        tweeted_at=tweeted_at,
    )

    result = tweet_repo.add(new_tweet)

    assert result.id is not None
    assert result.tweet_id == "newtweet123"
    assert result.content == "New test tweet"
    assert result.author_username == "testuser"
    assert result.media_urls == ["https://example.com/pic.png"]
    assert result.thread_id == sample_thread_id
    assert result.position_in_thread == 0


def test_add_duplicate_tweet_id_returns_existing(
    tweet_repo: TweetRepository, sample_thread_id: int
):
    """Test that adding a tweet with duplicate tweet_id returns the existing tweet."""
    tweeted_at = datetime.now(timezone.utc)

    # Add first tweet
    first_tweet = TweetCreate(
        tweet_id="duplicate_tweet_id",
        author_username="testuser",
        author_display_name="Test User",
        content="First tweet",
        media_urls=[],
        thread_id=sample_thread_id,
        position_in_thread=0,
        tweeted_at=tweeted_at,
    )
    result1 = tweet_repo.add(first_tweet)

    # Try to add second tweet with same tweet_id but different content
    second_tweet = TweetCreate(
        tweet_id="duplicate_tweet_id",
        author_username="differentuser",
        author_display_name="Different User",
        content="Different content",
        media_urls=["https://example.com/img.jpg"],
        thread_id=sample_thread_id,
        position_in_thread=1,
        tweeted_at=tweeted_at,
    )
    result2 = tweet_repo.add(second_tweet)

    # Should return the same tweet
    assert result1.id == result2.id
    assert result2.content == "First tweet"  # Original content preserved
    assert result2.author_username == "testuser"  # Original values preserved


def test_get_existing_tweet(
    tweet_repo: TweetRepository,
    sample_tweets: list[TweetRead],
    sample_thread_id: int,
):
    """Test getting a tweet by ID and thread ID when it exists."""
    tweet = sample_tweets[0]

    result = tweet_repo.get(tweet.id, sample_thread_id)

    assert result is not None
    assert result.id == tweet.id
    assert result.content == "First tweet in thread"
    assert result.thread_id == sample_thread_id


def test_get_tweet_wrong_thread_id(
    tweet_repo: TweetRepository, sample_tweets: list[TweetRead]
):
    """Test getting a tweet with wrong thread ID returns None."""
    tweet = sample_tweets[0]

    result = tweet_repo.get(tweet.id, 999)

    assert result is None


def test_get_by_id_success(tweet_repo: TweetRepository, sample_tweets: list[TweetRead]):
    """Test getting a tweet by ID when it exists."""
    result = tweet_repo.get_by_id(sample_tweets[0].id)

    assert result is not None
    assert result.id == sample_tweets[0].id
    assert result.content == "First tweet in thread"
    assert result.tweet_id == "tweet001"


def test_get_by_id_not_found(tweet_repo: TweetRepository):
    """Test getting a tweet by ID when it doesn't exist."""
    result = tweet_repo.get_by_id(999)
    assert result is None


def test_get_by_tweet_id_success(
    tweet_repo: TweetRepository, sample_tweets: list[TweetRead]
):
    """Test getting a tweet by Twitter's tweet_id when it exists."""
    result = tweet_repo.get_by_tweet_id("tweet002")

    assert result is not None
    assert result.tweet_id == "tweet002"
    assert result.content == "Second tweet in thread"
    assert result.media_urls == ["https://example.com/image.jpg"]


def test_get_by_tweet_id_not_found(tweet_repo: TweetRepository):
    """Test getting a tweet by Twitter's tweet_id when it doesn't exist."""
    result = tweet_repo.get_by_tweet_id("nonexistent")
    assert result is None


def test_get_by_thread_id(
    tweet_repo: TweetRepository,
    tweet_thread_repo: TweetThreadRepository,
    sample_tweets: list[TweetRead],
    sample_thread_id: int,
):
    """Test getting tweets by thread ID."""
    # Create a second thread with its own tweet
    thread2 = TweetThreadCreate(
        root_tweet_id="other_thread",
        author_username="user2",
        author_display_name="User Two",
        title="Other Thread",
    )
    thread2_created = tweet_thread_repo.add(thread2)

    tweet2 = TweetCreate(
        tweet_id="thread2_tweet",
        author_username="user2",
        author_display_name="User Two",
        content="Tweet in thread 2",
        media_urls=[],
        thread_id=thread2_created.id,
        position_in_thread=0,
        tweeted_at=datetime.now(timezone.utc),
    )
    tweet_repo.add(tweet2)

    # Get tweets for first thread (should return sample_tweets)
    thread1_tweets = tweet_repo.get_by_thread_id(sample_thread_id)
    assert len(thread1_tweets) == 3  # sample_tweets has 3 tweets
    contents = [t.content for t in thread1_tweets]
    assert "First tweet in thread" in contents
    assert "Second tweet in thread" in contents
    assert "Third tweet in thread" in contents

    # Verify tweets are ordered by position_in_thread
    assert thread1_tweets[0].position_in_thread == 0
    assert thread1_tweets[1].position_in_thread == 1
    assert thread1_tweets[2].position_in_thread == 2

    # Get tweets for second thread
    thread2_tweets = tweet_repo.get_by_thread_id(thread2_created.id)
    assert len(thread2_tweets) == 1
    assert thread2_tweets[0].content == "Tweet in thread 2"


def test_get_by_thread_id_empty(tweet_repo: TweetRepository):
    """Test getting tweets by thread ID when no tweets exist."""
    tweets = tweet_repo.get_by_thread_id(999)
    assert tweets == []


def test_get_random(tweet_repo: TweetRepository, sample_tweets: list[TweetRead]):
    """Test getting a random tweet."""
    random_tweet = tweet_repo.get_random()

    assert random_tweet is not None
    assert random_tweet.id in [t.id for t in sample_tweets]


def test_get_random_empty(tweet_repo: TweetRepository):
    """Test getting a random tweet when database is empty."""
    random_tweet = tweet_repo.get_random()
    assert random_tweet is None


def test_get_random_no_embeddings(tweet_repo: TweetRepository, sample_thread_id: int):
    """Test getting a random tweet when tweets have no embeddings."""
    # Add tweet without embedding
    tweet = TweetCreate(
        tweet_id="no_embed_tweet",
        author_username="testuser",
        author_display_name="Test User",
        content="Tweet without embedding",
        media_urls=[],
        thread_id=sample_thread_id,
        position_in_thread=0,
        tweeted_at=datetime.now(timezone.utc),
        embedding=None,
    )
    tweet_repo.add(tweet)

    # Should return None since no tweets have embeddings
    random_tweet = tweet_repo.get_random()
    assert random_tweet is None


def test_find_similar_tweets_no_embedding(
    tweet_repo: TweetRepository, session: Session, sample_thread_id: int
):
    """Test find_similar_tweets when the tweet has no embedding."""
    tweet = TweetCreate(
        tweet_id="no_embed",
        author_username="testuser",
        author_display_name="Test User",
        content="No embedding",
        media_urls=[],
        thread_id=sample_thread_id,
        position_in_thread=0,
        tweeted_at=datetime.now(timezone.utc),
        embedding=None,
    )
    tweet_read = tweet_repo.add(tweet)

    similar = tweet_repo.find_similar_tweets(tweet_read, limit=5)
    assert similar == []


def test_get_tweet_counts_by_thread_ids(
    tweet_repo: TweetRepository,
    tweet_thread_repo: TweetThreadRepository,
    sample_tweets: list[TweetRead],
    sample_thread_id: int,
):
    """Test getting tweet counts for multiple threads."""
    # sample_tweets fixture creates 3 tweets for sample_thread_id
    # Create additional threads
    thread2 = TweetThreadCreate(
        root_tweet_id="thread2_root",
        author_username="user2",
        author_display_name="User Two",
        title="Thread 2",
    )
    thread3 = TweetThreadCreate(
        root_tweet_id="thread3_root",
        author_username="user3",
        author_display_name="User Three",
        title="Thread 3",
    )
    thread2_created = tweet_thread_repo.add(thread2)
    thread3_created = tweet_thread_repo.add(thread3)

    # Add tweets to thread2 (thread1 already has 3 from fixture)
    tweeted_at = datetime.now(timezone.utc)
    tweet4 = TweetCreate(
        tweet_id="t2c1",
        author_username="user2",
        author_display_name="User Two",
        content="Thread 2 tweet 1",
        media_urls=[],
        thread_id=thread2_created.id,
        position_in_thread=0,
        tweeted_at=tweeted_at,
    )
    tweet5 = TweetCreate(
        tweet_id="t2c2",
        author_username="user2",
        author_display_name="User Two",
        content="Thread 2 tweet 2",
        media_urls=[],
        thread_id=thread2_created.id,
        position_in_thread=1,
        tweeted_at=tweeted_at,
    )
    # Thread 3 has no tweets

    tweet_repo.add(tweet4)
    tweet_repo.add(tweet5)

    # Get counts for all threads
    counts = tweet_repo.get_tweet_counts_by_thread_ids(
        [sample_thread_id, thread2_created.id, thread3_created.id]
    )

    assert counts[sample_thread_id] == 3  # From sample_tweets fixture
    assert counts[thread2_created.id] == 2
    # Thread 3 should not appear in results (no tweets)
    assert thread3_created.id not in counts


def test_get_tweet_counts_by_thread_ids_empty_list(tweet_repo: TweetRepository):
    """Test getting tweet counts with empty thread ID list."""
    counts = tweet_repo.get_tweet_counts_by_thread_ids([])
    assert counts == {}


def test_get_tweet_counts_by_thread_ids_nonexistent_threads(
    tweet_repo: TweetRepository,
):
    """Test getting tweet counts for threads that don't exist."""
    counts = tweet_repo.get_tweet_counts_by_thread_ids([999, 1000])
    assert counts == {}


def test_delete_by_thread_id(
    tweet_repo: TweetRepository, sample_tweets: list[TweetRead], sample_thread_id: int
):
    """Test deleting all tweets for a thread."""
    assert tweet_repo.get_by_thread_id(sample_thread_id) != []

    tweet_repo.delete_by_thread_id(sample_thread_id)

    assert tweet_repo.get_by_thread_id(sample_thread_id) == []


def test_count_with_embeddings(
    tweet_repo: TweetRepository,
    sample_tweets: list[TweetRead],
    sample_thread_id: int,
):
    """Test counting tweets with embeddings in a mixed scenario."""
    # sample_tweets has 3 tweets with embeddings
    # Add 1 tweet WITHOUT embedding to test mixed scenario
    tweet_repo.add(
        TweetCreate(
            tweet_id="no_emb_tweet",
            author_username="testuser",
            author_display_name="Test User",
            content="Without embedding",
            media_urls=[],
            thread_id=sample_thread_id,
            position_in_thread=3,
            tweeted_at=datetime.now(timezone.utc),
            embedding=None,
        )
    )

    # Should only count the 3 from sample_tweets (not the one without embedding)
    count = tweet_repo.count_with_embeddings()
    assert count == 3


def test_count_with_embeddings_empty(tweet_repo: TweetRepository):
    """Test counting tweets with embeddings when none exist."""
    count = tweet_repo.count_with_embeddings()
    assert count == 0


def test_count_with_embeddings_all_have_embeddings(
    tweet_repo: TweetRepository, sample_tweets: list[TweetRead]
):
    """Test counting tweets when all have embeddings."""
    # sample_tweets fixture creates 3 tweets, all with embeddings
    count = tweet_repo.count_with_embeddings()
    assert count == 3
