"""
Tests for /random/v2 endpoint with unified schema supporting notes, URL chunks, and tweets.

Tests the weighted random selection between Kindle notes, URL chunks, and tweets,
unified response schema, SSE streaming, and background evaluation.
"""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.repositories.models import (
    BookCreate,
    NoteCreate,
    URLChunkCreate,
    URLCreate,
)
from src.routers.conftest import RandomV2DepsSetup
from src.test_utils import make_thread_and_tweets

client = TestClient(app)


def test_random_v2_no_content_returns_404(setup_random_v2_deps: RandomV2DepsSetup):
    """Test GET /random/v2 returns 404 when no content exists."""
    setup_random_v2_deps()

    response = client.get("/random/v2")

    assert response.status_code == 404
    data = response.json()
    assert data["detail"] == "No content found"


@pytest.mark.asyncio
async def test_random_v2_note_response_structure(
    setup_random_v2_deps: RandomV2DepsSetup,
):
    """Test GET /random/v2 returns correct unified schema for note."""
    book_repo, note_repo, _, _, _, _, _ = setup_random_v2_deps()

    # Create test data
    book = book_repo.add(BookCreate(title="Test Book", author="Test Author"))
    note = note_repo.add(
        NoteCreate(
            book_id=book.id,
            content="Test note content",
            content_hash="hash1",
            embedding=[0.1] * 1536,
        )
    )

    # Make async SSE streaming request
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        async with async_client.stream(
            "GET", "/random/v2", headers={"Accept": "text/event-stream"}
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]

            # Parse SSE events
            events: list[dict[str, Any]] = []
            event_type: str = ""
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    event_type = line[7:]
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
                    events.append({"type": event_type, "data": data})

            # Verify event sequence
            assert len(events) >= 2
            assert events[0]["type"] == "metadata"
            assert events[-1]["type"] == "context_complete"

            # Verify metadata structure for note
            metadata = events[0]["data"]

            # Verify source is a book (note type)
            assert metadata["source"]["id"] == book.id
            assert metadata["source"]["title"] == book.title

            # Verify content is the note
            assert metadata["content"]["id"] == note.id
            assert metadata["content"]["content"] == "Test note content"

            assert metadata["related_items"] == []


@pytest.mark.asyncio
async def test_random_v2_chunk_response_structure(
    setup_random_v2_deps: RandomV2DepsSetup,
):
    """Test GET /random/v2 returns correct unified schema for URL chunk."""
    _, _, _, url_repo, chunk_repo, _, _ = setup_random_v2_deps()

    # Create test data
    url = url_repo.add(URLCreate(url="https://example.com", title="Example"))
    chunk = chunk_repo.add(
        URLChunkCreate(
            content="Test chunk content",
            content_hash="hash1",
            url_id=url.id,
            chunk_order=0,
            is_summary=False,
            embedding=[0.1] * 1536,
        )
    )

    # Make async SSE streaming request
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        async with async_client.stream(
            "GET", "/random/v2", headers={"Accept": "text/event-stream"}
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]

            # Parse SSE events
            events: list[dict[str, Any]] = []
            event_type: str = ""
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    event_type = line[7:]
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
                    events.append({"type": event_type, "data": data})

            # Verify event sequence
            assert len(events) >= 2
            assert events[0]["type"] == "metadata"
            assert events[-1]["type"] == "context_complete"

            # Verify metadata structure for chunk
            metadata = events[0]["data"]

            # Verify source is a URL (chunk type)
            assert metadata["source"]["id"] == url.id
            assert metadata["source"]["url"] == url.url

            # Verify content is the chunk
            assert metadata["content"]["id"] == chunk.id
            assert metadata["content"]["content"] == "Test chunk content"

            assert metadata["related_items"] == []


@pytest.mark.asyncio
async def test_random_v2_tweet_response_structure(
    setup_random_v2_deps: RandomV2DepsSetup,
):
    """Test GET /random/v2 returns correct unified schema for tweet."""
    _, _, _, _, _, thread_repo, tweet_repo = setup_random_v2_deps()

    thread, tweets = make_thread_and_tweets(
        thread_repo,
        tweet_repo,
        tweet_contents=["This is a test tweet about programming"],
        root_tweet_id="root123",
        embedding=[0.1] * 1536,
    )
    tweet = tweets[0]

    # Make async SSE streaming request
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        async with async_client.stream(
            "GET", "/random/v2", headers={"Accept": "text/event-stream"}
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]

            # Parse SSE events
            events: list[dict[str, Any]] = []
            event_type: str = ""
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    event_type = line[7:]
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
                    events.append({"type": event_type, "data": data})

            # Verify event sequence
            assert len(events) >= 2
            assert events[0]["type"] == "metadata"
            assert events[-1]["type"] == "context_complete"

            # Verify metadata structure for tweet
            metadata = events[0]["data"]

            # Verify source is a tweet thread
            assert metadata["source"]["id"] == thread.id
            assert metadata["source"]["type"] == "tweet_thread"
            assert metadata["source"]["root_tweet_id"] == "root123"

            # Verify content is the tweet
            assert metadata["content"]["id"] == tweet.id
            assert metadata["content"]["content_type"] == "tweet"
            assert (
                metadata["content"]["content"]
                == "This is a test tweet about programming"
            )
