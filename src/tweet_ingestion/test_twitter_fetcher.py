"""
Tests for Twitter fetching and content extraction functionality.
"""

import pytest
import respx
from httpx import Response

from src.tweet_ingestion.interfaces import (
    TwitterFetchError,
    TweetNotFoundError,
    RateLimitError,
    ThreadTooLargeError,
)
from src.tweet_ingestion.twitter_fetcher import (
    fetch_thread,
    parse_tweet_input,
)


class TestParseTweetInput:
    """Tests for parse_tweet_input function."""

    def test_parse_tweet_id_directly(self):
        """Test parsing a numeric tweet ID."""
        assert parse_tweet_input("1234567890") == "1234567890"

    def test_parse_twitter_url(self):
        """Test parsing a twitter.com URL."""
        url = "https://twitter.com/user/status/1234567890"
        assert parse_tweet_input(url) == "1234567890"

    def test_parse_x_url(self):
        """Test parsing an x.com URL."""
        url = "https://x.com/user/status/9876543210"
        assert parse_tweet_input(url) == "9876543210"

    def test_parse_invalid_input_raises_error(self):
        """Test that invalid input raises TwitterFetchError."""
        with pytest.raises(TwitterFetchError, match="Invalid tweet input"):
            parse_tweet_input("not-a-valid-tweet")

    def test_parse_invalid_url_raises_error(self):
        """Test that invalid URL raises TwitterFetchError."""
        with pytest.raises(TwitterFetchError, match="Invalid tweet input"):
            parse_tweet_input("https://example.com/something")


SAMPLE_THREAD_SEARCH_RESPONSE = {
    "data": [
        {
            "id": "1234567890",
            "text": "Thread tweet 1/3 preview",
            "note_tweet": {"text": "Thread note tweet 1/3"},
            "author_id": "12345",
            "conversation_id": "1234567890",
            "created_at": "2024-01-15T10:00:00.000Z",
        },
        {
            "id": "1234567891",
            "text": "Thread tweet 2/3 preview",
            "note_tweet": {"text": "Thread note tweet 2/3"},
            "author_id": "12345",
            "conversation_id": "1234567890",
            "created_at": "2024-01-15T10:01:00.000Z",
        },
        {
            "id": "1234567892",
            "text": "Thread tweet 3/3 preview",
            "note_tweet": {"text": "Thread note tweet 3/3"},
            "author_id": "12345",
            "conversation_id": "1234567890",
            "created_at": "2024-01-15T10:02:00.000Z",
        },
    ],
    "includes": {
        "users": [{"id": "12345", "username": "threadauthor", "name": "Thread Author"}]
    },
}


class TestFetchThread:
    """Tests for fetch_thread function."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_fetch_single_tweet_thread(self):
        """Test fetching a single tweet (no thread)."""
        single_tweet_response = {
            "data": {
                "id": "1234567890",
                "text": "Just a preview",
                "note_tweet": {"text": "Just a single tweet"},
                "author_id": "12345",
                "created_at": "2024-01-15T10:00:00.000Z",
            },
            "includes": {
                "users": [
                    {"id": "12345", "username": "singleuser", "name": "Single User"}
                ]
            },
        }

        respx.get("https://api.twitter.com/2/tweets/1234567890").mock(
            return_value=Response(200, json=single_tweet_response)
        )

        result = await fetch_thread("1234567890", bearer_token="test_token")

        assert result.root_tweet_id == "1234567890"
        assert len(result.tweets) == 1
        assert result.tweets[0].content == "Just a single tweet"
        assert result.author_username == "singleuser"

    @pytest.mark.asyncio
    @respx.mock
    async def test_fetch_single_tweet_thread_falls_back_to_text_when_note_tweet_empty(
        self,
    ):
        """Test that empty note_tweet falls back to text."""
        response = {
            "data": {
                "id": "1234567894",
                "text": "Fallback preview text",
                "note_tweet": {"text": ""},
                "author_id": "12345",
                "created_at": "2024-01-15T10:04:00.000Z",
            },
            "includes": {
                "users": [
                    {"id": "12345", "username": "fallbackuser", "name": "Fallback User"}
                ]
            },
        }

        respx.get("https://api.twitter.com/2/tweets/1234567894").mock(
            return_value=Response(200, json=response)
        )

        result = await fetch_thread("1234567894", bearer_token="test_token")

        assert result.tweets[0].content == "Fallback preview text"
        assert result.author_username == "fallbackuser"

    @pytest.mark.asyncio
    @respx.mock
    async def test_fetch_thread_via_conversation_search(self):
        """Test fetching a multi-tweet thread."""
        # First call: get initial tweet
        initial_tweet_response = {
            "data": {
                "id": "1234567892",
                "text": "Thread tweet 3/3 preview",
                "note_tweet": {"text": "Thread note tweet 3/3"},
                "author_id": "12345",
                "conversation_id": "1234567890",
                "created_at": "2024-01-15T10:02:00.000Z",
            },
            "includes": {
                "users": [
                    {"id": "12345", "username": "threadauthor", "name": "Thread Author"}
                ]
            },
        }

        respx.get("https://api.twitter.com/2/tweets/1234567892").mock(
            return_value=Response(200, json=initial_tweet_response)
        )

        # Second call: search conversation
        respx.get("https://api.twitter.com/2/tweets/search/recent").mock(
            return_value=Response(200, json=SAMPLE_THREAD_SEARCH_RESPONSE)
        )

        result = await fetch_thread("1234567892", bearer_token="test_token")

        # Should have all 3 tweets
        assert len(result.tweets) == 3
        assert result.root_tweet_id == "1234567890"
        assert result.author_username == "threadauthor"

        # Tweets should be sorted by created_at
        assert result.tweets[0].content == "Thread note tweet 1/3"
        assert result.tweets[1].content == "Thread note tweet 2/3"
        assert result.tweets[2].content == "Thread note tweet 3/3"

    @pytest.mark.asyncio
    @respx.mock
    async def test_fetch_thread_empty_search_results(self):
        """Test thread fetch when search returns no results."""
        initial_tweet_response = {
            "data": {
                "id": "1234567890",
                "text": "Initial tweet preview",
                "note_tweet": {"text": "Initial note tweet"},
                "author_id": "12345",
                "conversation_id": "1234567890",
                "created_at": "2024-01-15T10:00:00.000Z",
            },
            "includes": {
                "users": [{"id": "12345", "username": "testuser", "name": "Test User"}]
            },
        }

        respx.get("https://api.twitter.com/2/tweets/1234567890").mock(
            return_value=Response(200, json=initial_tweet_response)
        )

        # Empty search result
        respx.get("https://api.twitter.com/2/tweets/search/recent").mock(
            return_value=Response(200, json={"meta": {"result_count": 0}})
        )

        result = await fetch_thread("1234567890", bearer_token="test_token")

        # Should fall back to just the initial tweet
        assert len(result.tweets) == 1
        assert result.tweets[0].tweet_id == "1234567890"
        assert result.tweets[0].content == "Initial note tweet"

    @pytest.mark.asyncio
    @respx.mock
    async def test_fetch_thread_tweet_not_found(self):
        """Test that TweetNotFoundError propagates from thread fetch."""
        respx.get("https://api.twitter.com/2/tweets/9999999999").mock(
            return_value=Response(404)
        )

        with pytest.raises(TweetNotFoundError):
            await fetch_thread("9999999999", bearer_token="test_token")

    @pytest.mark.asyncio
    @respx.mock
    async def test_fetch_thread_rate_limited(self):
        """Test rate limit handling in thread fetch."""
        respx.get("https://api.twitter.com/2/tweets/1234567890").mock(
            return_value=Response(429, headers={"retry-after": "120"})
        )

        with pytest.raises(RateLimitError) as exc_info:
            await fetch_thread("1234567890", bearer_token="test_token")

        assert exc_info.value.retry_after == 120

    @pytest.mark.asyncio
    @respx.mock
    async def test_fetch_thread_respects_max_depth(self):
        """Test that thread fetching respects max_depth parameter."""
        # Create response with many tweets
        many_tweets = {
            "data": [
                {
                    "id": f"tweet_{i}",
                    "text": f"Tweet {i}",
                    "author_id": "12345",
                    "conversation_id": "root_tweet",
                    "created_at": f"2024-01-15T10:{i:02d}:00.000Z",
                }
                for i in range(10)
            ],
            "includes": {
                "users": [
                    {"id": "12345", "username": "prolific", "name": "Prolific User"}
                ]
            },
        }

        initial_response = {
            "data": {
                "id": "root_tweet",
                "text": "Root tweet",
                "author_id": "12345",
                "conversation_id": "root_tweet",
                "created_at": "2024-01-15T10:00:00.000Z",
            },
            "includes": {
                "users": [
                    {"id": "12345", "username": "prolific", "name": "Prolific User"}
                ]
            },
        }

        respx.get("https://api.twitter.com/2/tweets/root_tweet").mock(
            return_value=Response(200, json=initial_response)
        )

        respx.get("https://api.twitter.com/2/tweets/search/recent").mock(
            return_value=Response(200, json=many_tweets)
        )

        # Should raise ThreadTooLargeError when exceeding max_depth
        with pytest.raises(ThreadTooLargeError):
            await fetch_thread("root_tweet", max_depth=5, bearer_token="test_token")


class TestFetchThreadFallback:
    """Tests for recursive thread traversal fallback."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_fallback_to_recursive_on_search_error(self):
        """Test fallback to recursive traversal when search fails."""
        # Initial tweet with reply chain
        tweet_2 = {
            "data": {
                "id": "tweet_2",
                "text": "Reply tweet preview",
                "note_tweet": {"text": "Reply note tweet"},
                "author_id": "12345",
                "conversation_id": "tweet_1",
                "referenced_tweets": [{"type": "replied_to", "id": "tweet_1"}],
                "created_at": "2024-01-15T10:01:00.000Z",
            },
            "includes": {
                "users": [{"id": "12345", "username": "author", "name": "Author"}]
            },
        }

        tweet_1 = {
            "data": {
                "id": "tweet_1",
                "text": "Original tweet preview",
                "note_tweet": {"text": "Original note tweet"},
                "author_id": "12345",
                "conversation_id": "tweet_1",
                "created_at": "2024-01-15T10:00:00.000Z",
            },
            "includes": {
                "users": [{"id": "12345", "username": "author", "name": "Author"}]
            },
        }

        # Mock initial tweet fetch
        respx.get("https://api.twitter.com/2/tweets/tweet_2").mock(
            return_value=Response(200, json=tweet_2)
        )

        # Mock search failing (e.g., tweets older than 7 days)
        respx.get("https://api.twitter.com/2/tweets/search/recent").mock(
            return_value=Response(403)  # Forbidden - simulating access denied
        )

        # Mock recursive fetch of parent tweet
        respx.get("https://api.twitter.com/2/tweets/tweet_1").mock(
            return_value=Response(200, json=tweet_1)
        )

        result = await fetch_thread("tweet_2", bearer_token="test_token")

        # Should have both tweets via recursive traversal
        assert len(result.tweets) == 2
        assert result.root_tweet_id == "tweet_1"
        assert [tweet.content for tweet in result.tweets] == [
            "Original note tweet",
            "Reply note tweet",
        ]

    @pytest.mark.asyncio
    @respx.mock
    async def test_recursive_traversal_stops_at_different_author(self):
        """Test that recursive traversal stops when it encounters a different author."""
        # tweet_3 replies to tweet_2, which replies to tweet_1 (different author)
        tweet_3 = {
            "data": {
                "id": "tweet_3",
                "text": "My follow-up preview",
                "note_tweet": {"text": "My follow-up note tweet"},
                "author_id": "author_a",
                "conversation_id": "tweet_1",
                "referenced_tweets": [{"type": "replied_to", "id": "tweet_2"}],
                "created_at": "2024-01-15T10:02:00.000Z",
            },
            "includes": {
                "users": [{"id": "author_a", "username": "alice", "name": "Alice"}]
            },
        }

        tweet_2 = {
            "data": {
                "id": "tweet_2",
                "text": "Alice continues preview",
                "note_tweet": {"text": "Alice continues note tweet"},
                "author_id": "author_a",
                "conversation_id": "tweet_1",
                "referenced_tweets": [{"type": "replied_to", "id": "tweet_1"}],
                "created_at": "2024-01-15T10:01:00.000Z",
            },
            "includes": {
                "users": [{"id": "author_a", "username": "alice", "name": "Alice"}]
            },
        }

        tweet_1_different_author = {
            "data": {
                "id": "tweet_1",
                "text": "Someone else started this preview",
                "note_tweet": {"text": "Someone else note tweet"},
                "author_id": "author_b",
                "conversation_id": "tweet_1",
                "created_at": "2024-01-15T10:00:00.000Z",
            },
            "includes": {
                "users": [{"id": "author_b", "username": "bob", "name": "Bob"}]
            },
        }

        respx.get("https://api.twitter.com/2/tweets/tweet_3").mock(
            return_value=Response(200, json=tweet_3)
        )
        respx.get("https://api.twitter.com/2/tweets/search/recent").mock(
            return_value=Response(403)
        )
        respx.get("https://api.twitter.com/2/tweets/tweet_2").mock(
            return_value=Response(200, json=tweet_2)
        )
        respx.get("https://api.twitter.com/2/tweets/tweet_1").mock(
            return_value=Response(200, json=tweet_1_different_author)
        )

        result = await fetch_thread("tweet_3", bearer_token="test_token")

        # Should only include alice's tweets, stopping before bob's tweet
        assert len(result.tweets) == 2
        assert all(t.author_username == "alice" for t in result.tweets)
        assert result.tweets[0].tweet_id == "tweet_2"
        assert result.tweets[1].tweet_id == "tweet_3"
        assert [tweet.content for tweet in result.tweets] == [
            "Alice continues note tweet",
            "My follow-up note tweet",
        ]
