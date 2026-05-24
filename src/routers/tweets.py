"""
Tweet-related endpoints for ingesting and exploring tweets with AI enhancements.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import SQLModel, Field
from src.tweet_ingestion.repositories.interfaces import (
    TweetThreadRepositoryInterface,
    TweetRepositoryInterface,
)
from src.llm_interface import LLMClientInterface
from src.embedding_interface import EmbeddingClientInterface
from src.tweet_ingestion.interfaces import (
    TwitterFetchError,
    TweetNotFoundError,
    RateLimitError,
    ThreadTooLargeError,
    ThreadFetcherFn,
)
from src.dependencies import (
    get_tweet_thread_repository,
    get_tweet_repository,
    get_twitter_fetcher,
    get_llm_client,
    get_embedding_client,
)
from src.repositories.models import (
    TweetThreadResponse,
    TweetThreadWithTweetsResponse,
    TweetResponse,
)
from src.tweet_ingestion.tweet_processor import process_tweet_content
from src.prompts import create_tweet_context_prompt, SYSTEM_INSTRUCTIONS
from src.context_generation.additional_context import get_additional_context_stream
from src.sse_utils import format_sse
from src.routers.response_builders import build_unified_response_for_tweet
from typing import AsyncGenerator
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tweets"])

# Constants
RELATED_TWEETS_LIMIT = 3


class TweetIngestRequest(SQLModel):
    """Request model for tweet ingestion."""

    tweet_input: str = Field(
        description="Tweet URL or tweet ID to ingest", max_length=2048
    )


class TweetThreadsListResponse(SQLModel):
    """Response model for listing tweet threads."""

    threads: list[TweetThreadResponse]


@router.post(
    "/tweets",
    summary="Ingest tweet or thread",
    description="""
    Process and store a tweet or tweet thread with embeddings.

    This endpoint:
    - Fetches the tweet and any thread context from Twitter
    - Generates an AI summary for multi-tweet threads
    - Generates embeddings for each tweet
    - Stores everything with deduplication

    If the thread has already been ingested, returns the existing record without re-fetching.
    """,
    response_description="Processing result with thread metadata and all tweets",
    responses={
        404: {"description": "Tweet not found (deleted or private)"},
        422: {"description": "Cannot process tweet (fetch error or thread too large)"},
        429: {"description": "Twitter rate limit exceeded"},
        200: {"description": "Tweet processed successfully"},
    },
)
async def ingest_tweet(
    request: TweetIngestRequest,
    thread_repository: TweetThreadRepositoryInterface = Depends(
        get_tweet_thread_repository
    ),
    tweet_repository: TweetRepositoryInterface = Depends(get_tweet_repository),
    llm_client: LLMClientInterface = Depends(get_llm_client),
    embedding_client: EmbeddingClientInterface = Depends(get_embedding_client),
    twitter_fetcher: ThreadFetcherFn = Depends(get_twitter_fetcher),
) -> TweetThreadWithTweetsResponse:
    """Ingest and process a tweet or tweet thread."""
    try:
        result = await process_tweet_content(
            request.tweet_input,
            thread_repository,
            tweet_repository,
            llm_client,
            embedding_client,
            fetch_fn=twitter_fetcher,
        )
        return result
    except TweetNotFoundError as e:
        logger.error(f"Tweet not found: {str(e)}")
        raise HTTPException(status_code=404, detail=f"Tweet not found: {str(e)}")
    except RateLimitError as e:
        logger.error(f"Twitter rate limit: {str(e)}")
        raise HTTPException(
            status_code=429, detail=f"Twitter rate limit exceeded: {str(e)}"
        )
    except ThreadTooLargeError as e:
        logger.error(f"Thread too large: {str(e)}")
        raise HTTPException(status_code=422, detail=f"Thread too large: {str(e)}")
    except TwitterFetchError as e:
        logger.error(f"Twitter fetch error: {str(e)}")
        raise HTTPException(status_code=422, detail=f"Cannot process tweet: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error processing tweet: {str(e)}")
        raise HTTPException(status_code=500, detail="An internal error occurred")


@router.get(
    "/tweets",
    summary="List all tweet threads",
    description="Retrieve all ingested tweet threads with tweet counts",
    response_description="List of tweet threads with metadata",
    response_model=TweetThreadsListResponse,
)
async def get_tweets(
    thread_repository: TweetThreadRepositoryInterface = Depends(
        get_tweet_thread_repository
    ),
) -> TweetThreadsListResponse:
    """List all tweet threads."""
    threads = thread_repository.list_threads()
    return TweetThreadsListResponse(threads=threads)


@router.get(
    "/tweets/{thread_id}",
    summary="Get tweet thread with all tweets",
    description="Retrieve a tweet thread and all its tweets sorted by position",
    response_description="Tweet thread metadata with all tweets",
    response_model=TweetThreadWithTweetsResponse,
    responses={
        404: {"description": "Tweet thread not found"},
        200: {"description": "Tweet thread retrieved successfully"},
    },
)
async def get_tweet_thread(
    thread_id: int,
    thread_repository: TweetThreadRepositoryInterface = Depends(
        get_tweet_thread_repository
    ),
    tweet_repository: TweetRepositoryInterface = Depends(get_tweet_repository),
) -> TweetThreadWithTweetsResponse:
    """Get a tweet thread with all its tweets."""
    thread = thread_repository.get(thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Tweet thread not found")

    tweets_read = tweet_repository.get_by_thread_id(thread_id)
    tweets = [TweetResponse.model_validate(t) for t in tweets_read]
    return TweetThreadWithTweetsResponse(thread=thread, tweets=tweets)


@router.get(
    "/tweets/{thread_id}/tweets/{tweet_id}",
    summary="Get tweet with streaming AI context",
    description="""
    Retrieve a specific tweet with AI-generated context streamed as Server-Sent Events.

    This endpoint:
    - Returns tweet content and metadata
    - Streams AI-powered additional context using SSE
    - Finds related tweets based on vector similarity

    **SSE Events:**
    - `metadata`: Contains thread, tweet, and related_tweets data
    - `context_chunk`: Chunks of AI-generated context as they arrive
    - `context_complete`: Signals the end of streaming
    - `error`: Error information if something goes wrong
    """,
    response_class=StreamingResponse,
)
async def get_tweet_with_context_stream(
    thread_id: int,
    tweet_id: int,
    thread_repository: TweetThreadRepositoryInterface = Depends(
        get_tweet_thread_repository
    ),
    tweet_repository: TweetRepositoryInterface = Depends(get_tweet_repository),
    llm_client: LLMClientInterface = Depends(get_llm_client),
) -> StreamingResponse:
    """Stream a tweet with AI-generated context."""

    # Fetch and validate data before streaming
    tweet = tweet_repository.get(id=tweet_id, thread_id=thread_id)
    if not tweet:
        logger.error(f"Error finding tweet with id {tweet_id} in thread {thread_id}")
        raise HTTPException(
            status_code=404,
            detail="Tweet not found or doesn't belong to the specified thread",
        )

    thread = thread_repository.get(thread_id)
    if not thread:
        logger.error(f"Error finding tweet thread with id {thread_id}")
        raise HTTPException(status_code=404, detail="Tweet thread not found")

    # Find similar tweets (from same thread)
    similar_tweets = tweet_repository.find_similar_tweets(
        tweet, limit=RELATED_TWEETS_LIMIT
    )

    # Prepare metadata using unified response builder
    metadata = build_unified_response_for_tweet(thread, tweet, similar_tweets)

    async def event_generator() -> AsyncGenerator[str, None]:
        # Send metadata first
        yield format_sse("metadata", metadata.model_dump(mode="json"))

        # Prepare prompt and instruction
        prompt = create_tweet_context_prompt(
            tweet.author_username,
            tweet.content,
            [similar_tweet.content for similar_tweet in similar_tweets],
        )
        instruction = SYSTEM_INSTRUCTIONS["context_provider"]

        # Stream context chunks
        try:
            async for stream_chunk in get_additional_context_stream(
                llm_client, prompt, instruction
            ):
                if not stream_chunk.is_complete:
                    yield format_sse("context_chunk", {"content": stream_chunk.content})
        except Exception as e:
            logger.error(f"Error streaming context: {e}")
            yield format_sse("error", {"detail": "Context generation failed"})
            return

        # Signal completion
        yield format_sse("context_complete", {})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
