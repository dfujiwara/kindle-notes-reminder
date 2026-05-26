"""
Twitter fetching and content extraction utilities.

Fetches tweets and threads using Twitter API v2 with Bearer Token authentication.
"""

import httpx
import logging
import re
from datetime import datetime
from typing import Any

from src.config import settings
from src.tweet_ingestion.interfaces import (
    FetchedThread,
    FetchedTweet,
    RateLimitError,
    ThreadTooLargeError,
    TweetNotFoundError,
    TwitterFetchError,
)

logger = logging.getLogger(__name__)

# Twitter API v2 endpoints
TWITTER_API_BASE = "https://api.twitter.com/2"


# URL patterns for twitter.com and x.com
TWEET_URL_PATTERN = re.compile(
    r"^https?://(?:www\.)?(?:twitter\.com|x\.com)/\w+/status/(\d+)"
)


def parse_tweet_input(input_str: str) -> str:
    """
    Parse tweet URL or ID into just the tweet ID.

    Args:
        input_str: Either a tweet URL or a tweet ID

    Returns:
        The tweet ID

    Raises:
        TwitterFetchError: If input is not a valid tweet URL or ID
    """
    # Try parsing as URL first
    match = TWEET_URL_PATTERN.match(input_str)
    if match:
        return match.group(1)

    # Check if it's a valid numeric ID
    if input_str.isdigit():
        return input_str

    raise TwitterFetchError(
        f"Invalid tweet input: {input_str}. "
        "Must be a tweet URL (twitter.com or x.com) or a numeric tweet ID."
    )


async def _fetch_tweet(
    tweet_id: str,
    bearer_token: str | None = None,
    timeout: int | None = None,
) -> FetchedTweet:
    """
    Fetch a single tweet by ID using Twitter API v2.

    Args:
        tweet_id: The Twitter-assigned tweet ID
        bearer_token: Twitter API Bearer Token (defaults to settings)
        timeout: Request timeout in seconds (defaults to settings)

    Returns:
        FetchedTweet with tweet content and metadata

    Raises:
        TwitterFetchError: If fetching fails
        TweetNotFoundError: If tweet is deleted or private
        RateLimitError: If rate limit exceeded
    """
    bearer_token = bearer_token or _get_bearer_token()
    timeout_val = timeout or settings.twitter_fetch_timeout

    url = f"{TWITTER_API_BASE}/tweets/{tweet_id}"
    params = {
        "tweet.fields": "author_id,conversation_id,created_at,referenced_tweets,attachments",
        "expansions": "author_id,attachments.media_keys,referenced_tweets.id",
        "user.fields": "username,name",
        "media.fields": "url,preview_image_url",
    }
    headers = {"Authorization": f"Bearer {bearer_token}"}

    try:
        async with httpx.AsyncClient(timeout=timeout_val) as client:
            logger.info(f"Fetching tweet: {tweet_id}")
            response = await client.get(url, params=params, headers=headers)

            if response.status_code == 429:
                retry_after = response.headers.get("retry-after")
                raise RateLimitError(
                    f"Rate limit exceeded for tweet {tweet_id}",
                    retry_after=int(retry_after) if retry_after else None,
                )

            if response.status_code == 404:
                raise TweetNotFoundError(f"Tweet not found: {tweet_id}")

            response.raise_for_status()
            data = response.json()

            if "errors" in data and "data" not in data:
                error_msg = data["errors"][0].get("detail", "Unknown error")
                if "not found" in error_msg.lower():
                    raise TweetNotFoundError(f"Tweet not found: {tweet_id}")
                raise TwitterFetchError(f"Twitter API error: {error_msg}")

            return _parse_tweet_response(data)

    except (TweetNotFoundError, RateLimitError, TwitterFetchError):
        raise
    except httpx.TimeoutException as e:
        raise TwitterFetchError(f"Timeout fetching tweet: {tweet_id}") from e
    except httpx.HTTPStatusError as e:
        raise TwitterFetchError(
            f"HTTP error {e.response.status_code} for tweet {tweet_id}"
        ) from e
    except httpx.RequestError as e:
        raise TwitterFetchError(f"Request failed for tweet {tweet_id}: {str(e)}") from e
    except Exception as e:
        logger.error(f"Unexpected error fetching tweet {tweet_id}: {str(e)}")
        raise TwitterFetchError(f"Error fetching tweet {tweet_id}: {str(e)}") from e


async def fetch_thread(
    tweet_id: str,
    max_depth: int = 50,
    bearer_token: str | None = None,
    timeout: int | None = None,
) -> FetchedThread:
    """
    Fetch a tweet thread starting from any tweet in the thread.

    Uses the conversation_id to fetch all tweets in the thread, then
    filters to only include tweets from the original author (true thread).

    Note: Twitter's search/recent endpoint only returns tweets from the last 7 days.
    For older threads, this falls back to recursive in_reply_to traversal.

    Args:
        tweet_id: The Twitter-assigned tweet ID (can be any tweet in thread)
        max_depth: Maximum number of tweets to fetch (default 50)
        bearer_token: Twitter API Bearer Token (defaults to settings)
        timeout: Request timeout in seconds (defaults to settings)

    Returns:
        FetchedThread with all tweets in conversation order

    Raises:
        TwitterFetchError: If fetching fails
        TweetNotFoundError: If tweet is deleted or private
        RateLimitError: If rate limit exceeded
        ThreadTooLargeError: If thread exceeds max_depth
    """
    bearer_token = bearer_token or _get_bearer_token()
    timeout_val = timeout or settings.twitter_fetch_timeout

    # First, fetch the initial tweet to get conversation_id and author
    initial_tweet = await _fetch_tweet(tweet_id, bearer_token, timeout_val)

    if not initial_tweet.conversation_id:
        # Single tweet, not part of a thread
        return FetchedThread(
            root_tweet_id=tweet_id,
            author_username=initial_tweet.author_username,
            author_display_name=initial_tweet.author_display_name,
            tweets=[initial_tweet],
        )

    # Try conversation search first (works for recent tweets)
    try:
        tweets = await _fetch_conversation_tweets(
            initial_tweet.conversation_id,
            initial_tweet.author_username,
            max_depth,
            bearer_token,
            timeout_val,
        )
        # If conversation search returns empty, fall back to initial tweet
        if not tweets:
            tweets = [initial_tweet]
    except TwitterFetchError:
        # Fall back to recursive traversal for older threads
        logger.info(
            f"Conversation search failed, falling back to recursive traversal for {tweet_id}"
        )
        tweets = await _fetch_thread_recursive(
            initial_tweet.conversation_id,
            initial_tweet,
            max_depth,
            bearer_token,
            timeout_val,
        )

    if len(tweets) > max_depth:
        raise ThreadTooLargeError(
            f"Thread exceeds max_depth ({len(tweets)} > {max_depth})"
        )

    # Sort by position (created_at)
    tweets.sort(key=lambda t: t.tweeted_at or datetime.min)

    # Find the root tweet
    root_tweet = tweets[0]

    return FetchedThread(
        root_tweet_id=root_tweet.tweet_id,
        author_username=root_tweet.author_username,
        author_display_name=root_tweet.author_display_name,
        tweets=tweets,
    )


async def _fetch_conversation_tweets(
    conversation_id: str,
    author_username: str,
    max_results: int,
    bearer_token: str,
    timeout: int,
) -> list[FetchedTweet]:
    """
    Fetch tweets in a conversation using search/recent endpoint.

    Note: Only returns tweets from the last 7 days.
    """
    url = f"{TWITTER_API_BASE}/tweets/search/recent"
    params = {
        "query": f"conversation_id:{conversation_id} from:{author_username}",
        "tweet.fields": "author_id,conversation_id,created_at,referenced_tweets,attachments",
        "expansions": "author_id,attachments.media_keys,referenced_tweets.id",
        "user.fields": "username,name",
        "media.fields": "url,preview_image_url",
        "max_results": min(max_results, 100),  # API limit is 100
    }
    headers = {"Authorization": f"Bearer {bearer_token}"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            logger.info(f"Searching conversation: {conversation_id}")
            response = await client.get(url, params=params, headers=headers)

            if response.status_code == 429:
                retry_after = response.headers.get("retry-after")
                raise RateLimitError(
                    f"Rate limit exceeded for conversation {conversation_id}",
                    retry_after=int(retry_after) if retry_after else None,
                )

            response.raise_for_status()
            data = response.json()

            if "data" not in data:
                return []

            # Build lookup maps for includes
            users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}
            media = {
                m["media_key"]: m for m in data.get("includes", {}).get("media", [])
            }

            tweets: list[FetchedTweet] = []
            for tweet_data in data["data"]:
                tweet = _parse_single_tweet(tweet_data, users, media)
                tweets.append(tweet)

            return tweets

    except RateLimitError:
        raise
    except httpx.HTTPStatusError as e:
        raise TwitterFetchError(
            f"HTTP error {e.response.status_code} searching conversation {conversation_id}"
        ) from e
    except httpx.RequestError as e:
        raise TwitterFetchError(
            f"Request failed for conversation {conversation_id}: {str(e)}"
        ) from e


async def _fetch_thread_recursive(
    conversation_id: str,
    start_tweet: FetchedTweet,
    max_depth: int,
    bearer_token: str,
    timeout: int,
) -> list[FetchedTweet]:
    """
    Fetch thread by recursively following in_reply_to chain.

    This is a fallback for threads older than 7 days.
    """
    tweets = [start_tweet]
    seen_ids = {start_tweet.tweet_id}
    author_username = start_tweet.author_username

    # Walk backwards to find root
    current = start_tweet
    while current.in_reply_to_tweet_id and len(tweets) < max_depth:
        if current.in_reply_to_tweet_id in seen_ids:
            break

        try:
            parent = await _fetch_tweet(
                current.in_reply_to_tweet_id, bearer_token, timeout
            )
            # Only include if same author (thread continuation)
            if parent.author_username == author_username:
                tweets.insert(0, parent)
                seen_ids.add(parent.tweet_id)
                current = parent
            else:
                # Different author means we've left the thread
                break
        except TweetNotFoundError:
            # Parent deleted, stop traversal
            break

    return tweets


def _get_bearer_token() -> str:
    """Get Twitter Bearer Token from settings."""
    token = settings.twitter_bearer_token
    if not token:
        raise TwitterFetchError(
            "Twitter Bearer Token not configured. "
            "Set TWITTER_BEARER_TOKEN environment variable."
        )
    return token.get_secret_value()


def _parse_tweet_response(data: dict[str, Any]) -> FetchedTweet:
    """Parse Twitter API v2 response for a single tweet."""
    tweet_data: dict[str, Any] = data["data"]
    includes: dict[str, Any] = data.get("includes", {})

    users: dict[str, dict[str, Any]] = {u["id"]: u for u in includes.get("users", [])}
    media: dict[str, dict[str, Any]] = {
        m["media_key"]: m for m in includes.get("media", [])
    }

    return _parse_single_tweet(tweet_data, users, media)


def _parse_single_tweet(
    tweet_data: dict[str, Any],
    users: dict[str, dict[str, Any]],
    media: dict[str, dict[str, Any]],
) -> FetchedTweet:
    """Parse a single tweet from API response data."""
    author_id = tweet_data.get("author_id", "")
    author = users.get(author_id, {})

    # Extract media URLs
    media_urls: list[str] = []
    attachments: dict[str, Any] = tweet_data.get("attachments", {})
    for media_key in attachments.get("media_keys", []):
        if media_key in media:
            media_item = media[media_key]
            url: str | None = media_item.get("url") or media_item.get(
                "preview_image_url"
            )
            if url:
                media_urls.append(url)

    # Extract in_reply_to_tweet_id from referenced_tweets (v2 API)
    in_reply_to_tweet_id: str | None = None
    for ref in tweet_data.get("referenced_tweets", []):
        if ref.get("type") == "replied_to":
            in_reply_to_tweet_id = ref["id"]
            break

    # Parse created_at
    created_at = datetime.fromisoformat(tweet_data["created_at"].replace("Z", "+00:00"))

    return FetchedTweet(
        tweet_id=tweet_data["id"],
        author_username=author.get("username", "unknown"),
        author_display_name=author.get("name", "Unknown"),
        content=tweet_data.get("text", ""),
        tweeted_at=created_at,
        media_urls=media_urls,
        conversation_id=tweet_data.get("conversation_id"),
        in_reply_to_tweet_id=in_reply_to_tweet_id,
    )
