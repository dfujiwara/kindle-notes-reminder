"""Tests for the tweet content processing pipeline."""

import pytest
from datetime import datetime, timezone

from src.tweet_ingestion.tweet_processor import process_tweet_content
from src.tweet_ingestion.interfaces import (
    FetchedThread,
    FetchedTweet,
    ThreadFetcherFn,
    TwitterFetchError,
)
from src.tweet_ingestion.conftest import (
    TweetProcessorDepsSetup,
    make_fetched_thread,
    make_fetched_tweet,
)
from src.embedding_interface import EmbeddingError
from src.repositories.models import TweetThreadCreate, TweetCreate


@pytest.mark.asyncio
async def test_process_single_tweet_success(
    single_tweet_fetcher: ThreadFetcherFn,
    setup_tweet_processor_deps: TweetProcessorDepsSetup,
) -> None:
    """Test successful processing of a single tweet."""
    thread_repo, tweet_repo, embedding_client, llm_client = setup_tweet_processor_deps()

    result = await process_tweet_content(
        "1234567890",
        thread_repo,
        tweet_repo,
        llm_client,
        embedding_client,
        fetch_fn=single_tweet_fetcher,
    )

    # Check thread
    assert result.thread.root_tweet_id == "1234567890"
    assert result.thread.author_username == "testuser"
    assert result.thread.tweet_count == 1

    # Check tweets
    assert len(result.tweets) == 1
    assert result.tweets[0].content == "This is a single tweet for testing."
    assert result.tweets[0].position_in_thread == 0


@pytest.mark.asyncio
async def test_process_multi_tweet_thread_success(
    multi_tweet_fetcher: ThreadFetcherFn,
    setup_tweet_processor_deps: TweetProcessorDepsSetup,
) -> None:
    """Test successful processing of a multi-tweet thread."""
    thread_repo, tweet_repo, embedding_client, llm_client = setup_tweet_processor_deps(
        llm_responses=["This is a thread about the topic."]
    )

    result = await process_tweet_content(
        "1111111111",
        thread_repo,
        tweet_repo,
        llm_client,
        embedding_client,
        fetch_fn=multi_tweet_fetcher,
    )

    # Check thread
    assert result.thread.root_tweet_id == "1111111111"
    assert result.thread.tweet_count == 3
    # Title should be LLM-generated summary of the first tweet only
    assert result.thread.title == "This is a thread about the topic."
    assert len(llm_client.prompts) == 1
    assert "exactly one sentence" in llm_client.prompts[0]
    assert "Thread 1/3: Introduction to the topic." in llm_client.prompts[0]
    assert "Thread 2/3: Deep dive into details." not in llm_client.prompts[0]

    # Check tweets
    assert len(result.tweets) == 3
    assert result.tweets[0].position_in_thread == 0
    assert result.tweets[1].position_in_thread == 1
    assert result.tweets[2].position_in_thread == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://twitter.com/user/status/1234567890",
        "https://x.com/user/status/1234567890",
    ],
)
async def test_process_tweet_with_url_input(
    url: str,
    single_tweet_fetcher: ThreadFetcherFn,
    setup_tweet_processor_deps: TweetProcessorDepsSetup,
) -> None:
    """Test processing with twitter.com and x.com URL inputs."""
    thread_repo, tweet_repo, embedding_client, llm_client = setup_tweet_processor_deps()

    result = await process_tweet_content(
        url,
        thread_repo,
        tweet_repo,
        llm_client,
        embedding_client,
        fetch_fn=single_tweet_fetcher,
    )

    assert result.thread.root_tweet_id == "1234567890"
    assert len(result.tweets) == 1


@pytest.mark.asyncio
async def test_process_duplicate_thread_returns_existing(
    setup_tweet_processor_deps: TweetProcessorDepsSetup,
) -> None:
    """Test that duplicate threads return existing record without re-saving.

    Note: The fetcher is still called to get the root_tweet_id for deduplication,
    but the existing data from the database is returned rather than re-processing.
    """
    thread_repo, tweet_repo, embedding_client, llm_client = setup_tweet_processor_deps()

    # Pre-populate with existing thread
    existing_thread = thread_repo.add(
        TweetThreadCreate(
            root_tweet_id="1234567890",
            author_username="existinguser",
            author_display_name="Existing User",
            title="Existing thread",
        )
    )
    thread_repo.update_tweet_count(existing_thread.id, 1)

    tweet_repo.add(
        TweetCreate(
            tweet_id="1234567890",
            author_username="existinguser",
            author_display_name="Existing User",
            content="Existing tweet content",
            media_urls=[],
            thread_id=existing_thread.id,
            position_in_thread=0,
            tweeted_at=datetime.now(timezone.utc),
        )
    )

    # Fetcher returns the same root_tweet_id as the existing thread
    # (simulating fetching a tweet that's already in the database)
    fetcher_called = False

    async def mock_fetcher(tweet_id: str, max_depth: int = 50) -> FetchedThread:
        nonlocal fetcher_called
        fetcher_called = True
        return make_fetched_thread(
            root_tweet_id="1234567890",
            tweets=[
                make_fetched_tweet(
                    tweet_id="1234567890",
                    content="Fresh content from API (should be ignored)",
                )
            ],
        )

    result = await process_tweet_content(
        "1234567890",
        thread_repo,
        tweet_repo,
        llm_client,
        embedding_client,
        fetch_fn=mock_fetcher,
    )

    # Fetcher should be called (to get root_tweet_id for deduplication)
    assert fetcher_called

    # Should return existing data from database, not the fetched data
    assert result.thread.title == "Existing thread"
    assert result.thread.author_username == "existinguser"
    assert len(result.tweets) == 1
    assert result.tweets[0].content == "Existing tweet content"

    # Should not have created new records
    assert len(thread_repo.threads) == 1
    assert len(tweet_repo.tweets) == 1


@pytest.mark.asyncio
async def test_process_tweet_generates_embeddings(
    multi_tweet_fetcher: ThreadFetcherFn,
    setup_tweet_processor_deps: TweetProcessorDepsSetup,
) -> None:
    """Test that embeddings are generated for all tweets."""
    thread_repo, tweet_repo, embedding_client, llm_client = setup_tweet_processor_deps(
        llm_responses=["Thread summary"]
    )

    await process_tweet_content(
        "1111111111",
        thread_repo,
        tweet_repo,
        llm_client,
        embedding_client,
        fetch_fn=multi_tweet_fetcher,
    )

    # Check all tweets have embeddings
    assert tweet_repo.count_with_embeddings() == 3


@pytest.mark.asyncio
async def test_process_tweet_fetch_error_propagates(
    failing_fetcher: ThreadFetcherFn,
    setup_tweet_processor_deps: TweetProcessorDepsSetup,
) -> None:
    """Test that fetch errors are propagated."""
    thread_repo, tweet_repo, embedding_client, llm_client = setup_tweet_processor_deps()

    with pytest.raises(TwitterFetchError, match="Failed to fetch tweet"):
        await process_tweet_content(
            "1234567890",
            thread_repo,
            tweet_repo,
            llm_client,
            embedding_client,
            fetch_fn=failing_fetcher,
        )


@pytest.mark.asyncio
async def test_process_tweet_invalid_input_raises_error(
    single_tweet_fetcher: ThreadFetcherFn,
    setup_tweet_processor_deps: TweetProcessorDepsSetup,
) -> None:
    """Test that invalid input raises error."""
    thread_repo, tweet_repo, embedding_client, llm_client = setup_tweet_processor_deps()

    with pytest.raises(TwitterFetchError, match="Invalid tweet input"):
        await process_tweet_content(
            "not-a-valid-tweet-or-url",
            thread_repo,
            tweet_repo,
            llm_client,
            embedding_client,
            fetch_fn=single_tweet_fetcher,
        )


@pytest.mark.asyncio
async def test_single_tweet_uses_content_as_title(
    single_tweet_fetcher: ThreadFetcherFn,
    setup_tweet_processor_deps: TweetProcessorDepsSetup,
) -> None:
    """Test that single tweets use truncated content as title."""
    thread_repo, tweet_repo, embedding_client, llm_client = setup_tweet_processor_deps()

    result = await process_tweet_content(
        "1234567890",
        thread_repo,
        tweet_repo,
        llm_client,
        embedding_client,
        fetch_fn=single_tweet_fetcher,
    )

    # Single tweet should use content as title (possibly truncated)
    assert "This is a single tweet" in result.thread.title


@pytest.mark.asyncio
async def test_process_tweet_with_media_urls(
    setup_tweet_processor_deps: TweetProcessorDepsSetup,
) -> None:
    """Test processing a tweet with media attachments."""
    thread_repo, tweet_repo, embedding_client, llm_client = setup_tweet_processor_deps()

    async def fetcher_with_media(tweet_id: str, max_depth: int = 50) -> FetchedThread:
        tweet = FetchedTweet(
            tweet_id=tweet_id,
            author_username="mediauser",
            author_display_name="Media User",
            content="Check out this image!",
            media_urls=[
                "https://pbs.twimg.com/media/image1.jpg",
                "https://pbs.twimg.com/media/image2.jpg",
            ],
            conversation_id=tweet_id,
            in_reply_to_tweet_id=None,
            tweeted_at=datetime.now(timezone.utc),
        )
        return make_fetched_thread(
            root_tweet_id=tweet_id,
            tweets=[tweet],
            author_username="mediauser",
            author_display_name="Media User",
        )

    result = await process_tweet_content(
        "9999999999",
        thread_repo,
        tweet_repo,
        llm_client,
        embedding_client,
        fetch_fn=fetcher_with_media,
    )

    assert len(result.tweets) == 1
    assert len(result.tweets[0].media_urls) == 2
    assert "image1.jpg" in result.tweets[0].media_urls[0]


@pytest.mark.asyncio
async def test_llm_failure_uses_fallback_title(
    multi_tweet_fetcher: ThreadFetcherFn,
    setup_tweet_processor_deps: TweetProcessorDepsSetup,
) -> None:
    """Test that LLM failure falls back to truncated first tweet."""
    thread_repo, tweet_repo, embedding_client, llm_client = setup_tweet_processor_deps(
        llm_should_fail=True
    )

    result = await process_tweet_content(
        "1111111111",
        thread_repo,
        tweet_repo,
        llm_client,
        embedding_client,
        fetch_fn=multi_tweet_fetcher,
    )

    # Should fall back to first tweet content
    assert "Introduction to the topic" in result.thread.title


@pytest.mark.asyncio
async def test_embedding_failure_propagates(
    multi_tweet_fetcher: ThreadFetcherFn,
    setup_tweet_processor_deps: TweetProcessorDepsSetup,
) -> None:
    """Test that embedding generation failure propagates as an error."""
    thread_repo, tweet_repo, embedding_client, llm_client = setup_tweet_processor_deps(
        embedding_should_fail=True, llm_responses=["Thread summary"]
    )

    with pytest.raises(EmbeddingError, match="Simulated embedding generation failure"):
        await process_tweet_content(
            "1111111111",
            thread_repo,
            tweet_repo,
            llm_client,
            embedding_client,
            fetch_fn=multi_tweet_fetcher,
        )


@pytest.mark.asyncio
async def test_embedding_failure_cleans_up_thread(
    multi_tweet_fetcher: ThreadFetcherFn,
    setup_tweet_processor_deps: TweetProcessorDepsSetup,
) -> None:
    """Test that a failed embedding run removes the orphaned thread record."""
    thread_repo, tweet_repo, embedding_client, llm_client = setup_tweet_processor_deps(
        embedding_should_fail=True, llm_responses=["Thread summary"]
    )

    with pytest.raises(EmbeddingError):
        await process_tweet_content(
            "1111111111",
            thread_repo,
            tweet_repo,
            llm_client,
            embedding_client,
            fetch_fn=multi_tweet_fetcher,
        )

    # Thread record should be rolled back; no orphaned records remain
    assert len(thread_repo.threads) == 0
    assert len(tweet_repo.tweets) == 0
