"""
Tests for tweet management endpoints in tweets.py

Tests tweet ingestion, listing, thread retrieval, and SSE streaming endpoints.
"""

import json
import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from typing import Any
from datetime import datetime, timezone

from src.routers.conftest import TweetDepsSetup
from src.test_utils import make_thread_and_tweets
from ..main import app
from ..repositories.models import TweetThreadCreate, TweetCreate

client = TestClient(app)


def test_ingest_tweet_fetch_error(setup_tweet_deps: TweetDepsSetup):
    """Test tweet fetch error returns 422."""
    _, _, fetcher = setup_tweet_deps(fetcher_should_fail=True)

    response = client.post("/tweets", json={"tweet_input": "123456789"})

    assert response.status_code == 422
    data = response.json()
    assert "detail" in data
    assert "Cannot process tweet" in data["detail"]
    assert len(fetcher.calls) == 1


def test_ingest_tweet_success(setup_tweet_deps: TweetDepsSetup):
    """Test successful tweet ingestion returns 200 with thread and tweets stored."""
    thread_repo, tweet_repo, _ = setup_tweet_deps()

    response = client.post("/tweets", json={"tweet_input": "123456789"})

    assert response.status_code == 200
    data = response.json()

    # Verify thread was stored (StubTwitterFetcher uses "tweet123" as default root_tweet_id)
    assert data["thread"]["root_tweet_id"] == "tweet123"
    assert data["thread"]["author_username"] == "test_user"
    assert data["thread"]["tweet_count"] == 1

    # Verify tweets were stored
    assert len(data["tweets"]) == 1
    assert data["tweets"][0]["content"] == "Test tweet content"
    assert data["tweets"][0]["author_username"] == "test_user"

    # Verify in repositories
    stored_thread = thread_repo.get_by_root_tweet_id("tweet123")
    assert stored_thread is not None
    stored_tweets = tweet_repo.get_by_thread_id(stored_thread.id)
    assert len(stored_tweets) == 1


def test_ingest_tweet_deduplication(setup_tweet_deps: TweetDepsSetup):
    """Test that ingesting the same tweet twice returns existing record."""
    _, _, fetcher = setup_tweet_deps()

    # Ingest the same tweet twice
    response1 = client.post("/tweets", json={"tweet_input": "123456789"})
    response2 = client.post("/tweets", json={"tweet_input": "123456789"})

    assert response1.status_code == 200
    assert response2.status_code == 200

    data1 = response1.json()
    data2 = response2.json()

    # Both should return the same thread
    assert data1["thread"]["id"] == data2["thread"]["id"]
    assert data1["thread"]["root_tweet_id"] == data2["thread"]["root_tweet_id"]

    # Fetcher is called for each request; deduplication happens at the DB layer
    assert len(fetcher.calls) == 2


def test_ingest_tweet_missing_input(setup_tweet_deps: TweetDepsSetup):
    """Test POST /tweets with missing tweet_input field returns 422."""
    setup_tweet_deps()

    response = client.post("/tweets", json={})

    assert response.status_code == 422


def test_get_tweets_empty(setup_tweet_deps: TweetDepsSetup):
    """Test GET /tweets returns empty list when no threads exist."""
    setup_tweet_deps()

    response = client.get("/tweets")

    assert response.status_code == 200
    data = response.json()
    assert len(data["threads"]) == 0


def test_get_tweets_with_threads(setup_tweet_deps: TweetDepsSetup):
    """Test GET /tweets returns all threads."""
    thread_repo, _, _ = setup_tweet_deps()

    # Create test threads
    thread1 = thread_repo.add(
        TweetThreadCreate(
            root_tweet_id="tweet001",
            author_username="user1",
            author_display_name="User One",
            title="Thread 1",
        )
    )
    thread2 = thread_repo.add(
        TweetThreadCreate(
            root_tweet_id="tweet002",
            author_username="user2",
            author_display_name="User Two",
            title="Thread 2",
        )
    )

    response = client.get("/tweets")

    assert response.status_code == 200
    data = response.json()
    assert len(data["threads"]) == 2

    thread_ids = {t["id"] for t in data["threads"]}
    assert thread1.id in thread_ids
    assert thread2.id in thread_ids

    # Verify thread data
    t1_response = next(t for t in data["threads"] if t["id"] == thread1.id)
    assert t1_response["root_tweet_id"] == "tweet001"
    assert t1_response["author_username"] == "user1"
    assert t1_response["title"] == "Thread 1"

    t2_response = next(t for t in data["threads"] if t["id"] == thread2.id)
    assert t2_response["root_tweet_id"] == "tweet002"
    assert t2_response["author_username"] == "user2"


def test_get_tweet_thread_not_found(setup_tweet_deps: TweetDepsSetup):
    """Test GET /tweets/{thread_id} returns 404 when thread doesn't exist."""
    setup_tweet_deps()

    response = client.get("/tweets/999")

    assert response.status_code == 404
    data = response.json()
    assert data["detail"] == "Tweet thread not found"


def test_get_tweet_thread_empty_tweets(setup_tweet_deps: TweetDepsSetup):
    """Test GET /tweets/{thread_id} returns thread with empty tweets list."""
    thread_repo, _, _ = setup_tweet_deps()

    thread = thread_repo.add(
        TweetThreadCreate(
            root_tweet_id="tweet001",
            author_username="user1",
            author_display_name="User One",
            title="My thread",
        )
    )

    response = client.get(f"/tweets/{thread.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["thread"]["id"] == thread.id
    assert data["thread"]["root_tweet_id"] == "tweet001"
    assert data["thread"]["title"] == "My thread"
    assert len(data["tweets"]) == 0


def test_get_tweet_thread_with_tweets(setup_tweet_deps: TweetDepsSetup):
    """Test GET /tweets/{thread_id} returns thread with all tweets sorted by position."""
    thread_repo, tweet_repo, _ = setup_tweet_deps()

    thread = thread_repo.add(
        TweetThreadCreate(
            root_tweet_id="tweet001",
            author_username="user1",
            author_display_name="User One",
            title="My thread",
        )
    )

    now = datetime.now(timezone.utc)

    # Add tweets in non-sequential order to verify ordering
    tweet_repo.add(
        TweetCreate(
            tweet_id="t2",
            author_username="user1",
            author_display_name="User One",
            content="Second tweet",
            thread_id=thread.id,
            position_in_thread=1,
            tweeted_at=now,
            embedding=[0.2] * 1536,
        )
    )
    tweet_repo.add(
        TweetCreate(
            tweet_id="t1",
            author_username="user1",
            author_display_name="User One",
            content="First tweet",
            thread_id=thread.id,
            position_in_thread=0,
            tweeted_at=now,
            embedding=[0.1] * 1536,
        )
    )

    response = client.get(f"/tweets/{thread.id}")

    assert response.status_code == 200
    data = response.json()

    assert data["thread"]["id"] == thread.id
    assert len(data["tweets"]) == 2

    # Verify tweets are sorted by position
    assert data["tweets"][0]["position_in_thread"] == 0
    assert data["tweets"][0]["content"] == "First tweet"
    assert data["tweets"][1]["position_in_thread"] == 1
    assert data["tweets"][1]["content"] == "Second tweet"


def test_get_tweet_with_context_stream_tweet_not_found(
    setup_tweet_deps: TweetDepsSetup,
):
    """Test GET /tweets/{thread_id}/tweets/{tweet_id} returns 404 when tweet not found."""
    setup_tweet_deps()

    response = client.get("/tweets/999/tweets/999")

    assert response.status_code == 404
    data = response.json()
    assert "Tweet not found" in data["detail"]


def test_get_tweet_with_context_stream_thread_not_found(
    setup_tweet_deps: TweetDepsSetup,
):
    """Test GET /tweets/{thread_id}/tweets/{tweet_id} returns 404 when thread not found."""
    _, tweet_repo, _ = setup_tweet_deps()

    # Add a tweet to a non-existent thread
    now = datetime.now(timezone.utc)
    tweet = tweet_repo.add(
        TweetCreate(
            tweet_id="t1",
            author_username="user1",
            author_display_name="User One",
            content="Test tweet",
            thread_id=999,
            position_in_thread=0,
            tweeted_at=now,
            embedding=[0.1] * 1536,
        )
    )

    response = client.get(f"/tweets/999/tweets/{tweet.id}")

    assert response.status_code == 404
    data = response.json()
    assert "Tweet thread not found" in data["detail"]


@pytest.mark.asyncio
async def test_get_tweet_with_context_stream_success(setup_tweet_deps: TweetDepsSetup):
    """Test GET /tweets/{thread_id}/tweets/{tweet_id} streams events correctly."""
    thread_repo, tweet_repo, _ = setup_tweet_deps()

    # Create test thread and tweet
    thread, tweets = make_thread_and_tweets(
        thread_repo,
        tweet_repo,
        tweet_contents=["Test tweet content"],
        root_tweet_id="tweet001",
        embedding=[0.1] * 1536,
    )
    tweet = tweets[0]

    # Make SSE streaming request
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        async with async_client.stream(
            "GET",
            f"/tweets/{thread.id}/tweets/{tweet.id}",
            headers={"Accept": "text/event-stream"},
        ) as response:
            assert response.status_code == 200
            assert (
                response.headers["content-type"] == "text/event-stream; charset=utf-8"
            )

            # Parse SSE events as they stream
            events: list[dict[str, Any]] = []
            event_type: str = ""
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    event_type = line[7:]  # Remove "event: " prefix
                elif line.startswith("data: "):
                    data = json.loads(line[6:])  # Remove "data: " prefix
                    events.append({"type": event_type, "data": data})

            # Verify event sequence: metadata -> context_chunk(s) -> context_complete
            assert len(events) >= 2
            assert events[0]["type"] == "metadata"
            assert events[-1]["type"] == "context_complete"

            # Verify metadata event contains expected structure
            metadata = events[0]["data"]
            assert metadata["source"]["id"] == thread.id
            assert metadata["source"]["root_tweet_id"] == "tweet001"
            assert metadata["source"]["type"] == "tweet_thread"
            assert metadata["content"]["id"] == tweet.id
            assert metadata["content"]["content"] == "Test tweet content"
            assert metadata["content"]["content_type"] == "tweet"
            assert "related_items" in metadata

            # Verify context_chunk events
            content_events = [e for e in events if e["type"] == "context_chunk"]
            assert len(content_events) >= 1
            full_content = "".join([e["data"]["content"] for e in content_events])
            assert full_content == "Test LLM response"
