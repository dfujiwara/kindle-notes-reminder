"""
Tweet content ingestion and processing pipeline.

Handles fetching tweets/threads, generating summaries and embeddings,
and storing everything in the database. Supports deduplication by root_tweet_id.
"""

import asyncio
import logging
from datetime import datetime, timezone

from src.prompts import SYSTEM_INSTRUCTIONS, create_tweet_title_prompt
from src.types import Embedding
from src.embedding_interface import EmbeddingClientInterface
from src.llm_interface import LLMClientInterface
from src.tweet_ingestion.repositories.interfaces import (
    TweetRepositoryInterface,
    TweetThreadRepositoryInterface,
)
from src.repositories.models import (
    TweetCreate,
    TweetRead,
    TweetResponse,
    TweetThreadCreate,
    TweetThreadResponse,
    TweetThreadWithTweetsResponse,
)
from src.tweet_ingestion.interfaces import (
    FetchedThread,
    FetchedTweet,
    ThreadFetcherFn,
)
from src.tweet_ingestion.twitter_fetcher import (
    fetch_thread,
    parse_tweet_input,
)

logger = logging.getLogger(__name__)


async def process_tweet_content(
    tweet_input: str,
    thread_repo: TweetThreadRepositoryInterface,
    tweet_repo: TweetRepositoryInterface,
    llm_client: LLMClientInterface,
    embedding_client: EmbeddingClientInterface,
    fetch_fn: ThreadFetcherFn | None = None,
    max_thread_depth: int = 50,
) -> TweetThreadWithTweetsResponse:
    """
    Process tweet content: fetch, summarize (if thread), embed, and store.

    This is a synchronous pipeline that blocks until all steps complete.
    If the thread already exists in the database, returns the existing record
    without re-fetching.

    Args:
        tweet_input: Tweet URL or tweet ID to ingest
        thread_repo: TweetThread repository for database operations
        tweet_repo: Tweet repository for database operations
        llm_client: LLM client for generating summaries
        embedding_client: Embedding client for generating vector embeddings
        fetch_fn: Twitter fetcher (defaults to fetch_thread)
        max_thread_depth: Maximum tweets to fetch in a thread (default 50)

    Returns:
        TweetThreadWithTweetsResponse containing the thread and all tweets

    Raises:
        TwitterFetchError: If tweet fetching fails
        Exception: If embedding generation or database operations fail
    """
    # Step 1: Parse input to get tweet ID
    tweet_id = parse_tweet_input(tweet_input)
    logger.info(f"Processing tweet: {tweet_id}")

    # Step 2: Fetch thread from Twitter
    fetcher = fetch_fn or fetch_thread
    fetched = await fetcher(tweet_id, max_thread_depth)
    logger.info(
        f"Fetched thread with {len(fetched.tweets)} tweets, "
        f"root_tweet_id: {fetched.root_tweet_id}"
    )

    # Step 3: Check if thread already exists (deduplication by root_tweet_id)
    existing_result = _handle_existing_thread(
        fetched.root_tweet_id, thread_repo, tweet_repo
    )
    if existing_result:
        logger.info(f"Thread already exists: {fetched.root_tweet_id}")
        return existing_result

    # Step 4: Generate thread title
    title = await _generate_thread_title(llm_client, fetched)
    logger.info(f"Generated thread title: {title}")

    # Step 5: Generate embeddings for all tweets in parallel (before any DB writes
    # so that failures here leave the database untouched)
    embeddings = await _generate_all_embeddings(embedding_client, fetched.tweets)

    # Step 6: Save thread to database
    thread_create = TweetThreadCreate(
        root_tweet_id=fetched.root_tweet_id,
        author_username=fetched.author_username,
        author_display_name=fetched.author_display_name,
        title=title,
    )
    saved_thread = thread_repo.add(thread_create)
    logger.info(f"Saved thread record with ID {saved_thread.id}")

    # Step 7: Save tweets to database
    saved_tweets = _save_tweets_to_database(
        tweet_repo, saved_thread, fetched.tweets, embeddings
    )

    # Step 8: Update thread tweet count
    tweet_count = len(saved_tweets)
    thread_repo.update_tweet_count(saved_thread.id, tweet_count)

    # Step 9: Build and return response
    result = _build_response(saved_thread, saved_tweets, tweet_count=tweet_count)
    logger.info(
        f"Successfully processed thread {fetched.root_tweet_id} "
        f"with {len(result.tweets)} tweets"
    )
    return result


def _handle_existing_thread(
    root_tweet_id: str,
    thread_repo: TweetThreadRepositoryInterface,
    tweet_repo: TweetRepositoryInterface,
) -> TweetThreadWithTweetsResponse | None:
    """Check if thread exists and return it if found (deduplication)."""
    existing_thread = thread_repo.get_by_root_tweet_id(root_tweet_id)
    if not existing_thread:
        return None

    tweets_read = tweet_repo.get_by_thread_id(existing_thread.id)
    return _build_response(existing_thread, tweets_read)


async def _generate_thread_title(
    llm_client: LLMClientInterface,
    fetched: FetchedThread,
) -> str:
    """
    Generate a title/summary for the thread.

    For single tweets, use the first 50 characters of the content.
    For multi-tweet threads, summarize only the first tweet with the LLM.
    """
    if len(fetched.tweets) == 1:
        # Single tweet: use truncated content as title
        content = fetched.tweets[0].content
        return content[:50] + "..." if len(content) > 50 else content

    first_tweet_content = fetched.tweets[0].content
    prompt = create_tweet_title_prompt(first_tweet_content)
    system_instruction = SYSTEM_INSTRUCTIONS["summarizer"]

    try:
        summary = await llm_client.get_response(prompt, system_instruction)
        summary_display = summary[:100] + "..." if len(summary) > 100 else summary
        logger.info(f"Generated thread title: {summary_display}")
        return summary
    except Exception as e:
        logger.error(f"Error generating thread title: {str(e)}")
        # Fallback to first tweet content truncated
        content = fetched.tweets[0].content
        return content[:50] + "..." if len(content) > 50 else content


async def _generate_all_embeddings(
    embedding_client: EmbeddingClientInterface,
    tweets: list[FetchedTweet],
) -> list[Embedding]:
    """Generate embeddings for all tweets in parallel."""
    logger.info(f"Generating {len(tweets)} embeddings")
    try:
        embedding_tasks = [
            embedding_client.generate_embedding(tweet.content) for tweet in tweets
        ]
        embeddings = await asyncio.gather(*embedding_tasks)
        logger.info("Successfully generated all embeddings")
        return embeddings
    except Exception as e:
        logger.error(f"Error during parallel embedding generation: {str(e)}")
        raise


def _save_tweets_to_database(
    tweet_repo: TweetRepositoryInterface,
    saved_thread: TweetThreadResponse,
    fetched_tweets: list[FetchedTweet],
    embeddings: list[Embedding],
) -> list[TweetRead]:
    """Save all tweets to database with their embeddings."""
    saved_tweets: list[TweetRead] = []

    for position, (fetched_tweet, embedding) in enumerate(
        zip(fetched_tweets, embeddings, strict=True)
    ):
        tweet_create = TweetCreate(
            tweet_id=fetched_tweet.tweet_id,
            author_username=fetched_tweet.author_username,
            author_display_name=fetched_tweet.author_display_name,
            content=fetched_tweet.content,
            media_urls=fetched_tweet.media_urls,
            thread_id=saved_thread.id,
            position_in_thread=position,
            # tweeted_at may be None if the API omits it (e.g. older tweets)
            tweeted_at=fetched_tweet.tweeted_at or datetime.now(timezone.utc),
            embedding=embedding,
        )
        saved_tweet = tweet_repo.add(tweet_create)
        saved_tweets.append(saved_tweet)
        logger.info(f"Saved tweet {position} (id={fetched_tweet.tweet_id}) to database")

    return saved_tweets


def _build_response(
    saved_thread: TweetThreadResponse,
    saved_tweets: list[TweetRead],
    tweet_count: int | None = None,
) -> TweetThreadWithTweetsResponse:
    """Build response from thread and tweets."""
    thread_response = (
        saved_thread.model_copy(update={"tweet_count": tweet_count})
        if tweet_count is not None
        else saved_thread
    )
    tweet_responses = [TweetResponse.model_validate(tweet) for tweet in saved_tweets]
    return TweetThreadWithTweetsResponse(
        thread=thread_response,
        tweets=tweet_responses,
    )
