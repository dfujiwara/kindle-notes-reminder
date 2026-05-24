"""
Tests for streaming SSE endpoints.

These tests verify the Server-Sent Events (SSE) streaming functionality
for the specific note endpoint (/books/{book_id}/notes/{note_id} in notes.py).
"""

import json
from typing import Any

import pytest
from httpx import AsyncClient, ASGITransport

from src.main import app
from src.repositories.models import NoteCreate, BookCreate
from src.routers.conftest import StreamingDepsSetup

evaluation_response = (
    "Score: 0.85\nEvaluation: Well-structured and informative response."
)


@pytest.mark.asyncio
async def test_get_note_with_context_stream_success(
    setup_streaming_deps: StreamingDepsSetup,
):
    """Test streaming specific note endpoint returns proper SSE events"""
    book_repo, note_repo, _, llm_client = setup_streaming_deps(
        llm_responses=["Specific note context", evaluation_response]
    )

    # Create a book
    book = BookCreate(title="Test Book", author="Test Author")
    book = book_repo.add(book)

    # Add notes
    note1 = NoteCreate(
        content="Specific note content",
        content_hash="hash1",
        book_id=book.id,
    )
    note2 = NoteCreate(
        content="Related note content",
        content_hash="hash2",
        book_id=book.id,
    )
    added_note1 = note_repo.add(note1)
    note_repo.add(note2)

    # Make streaming request for specific note
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "GET",
            f"/books/{book.id}/notes/{added_note1.id}",
            headers={"Accept": "text/event-stream"},
        ) as response:
            assert response.status_code == 200
            assert (
                response.headers["content-type"] == "text/event-stream; charset=utf-8"
            )

            events: list[dict[str, Any]] = []
            event_type = None
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

            # Verify metadata for specific note
            metadata = events[0]["data"]
            assert metadata["book"]["id"] == book.id
            assert metadata["note"]["id"] == added_note1.id
            assert metadata["note"]["content"] == "Specific note content"
            assert len(metadata["related_notes"]) == 1

            # Verify content streaming
            content_events = [e for e in events if e["type"] == "context_chunk"]
            assert len(content_events) > 0
            full_content = "".join([e["data"]["content"] for e in content_events])
            assert "Specific note context" in full_content

            # Verify similar notes were included in the LLM prompt
            assert llm_client.prompts
            assert "Related note content" in llm_client.prompts[0]


@pytest.mark.asyncio
async def test_get_note_with_context_stream_note_not_found(
    setup_streaming_deps: StreamingDepsSetup,
):
    """Test streaming specific note endpoint when note doesn't exist"""
    book_repo, _, _, _ = setup_streaming_deps()

    # Create a book
    book = BookCreate(title="Test Book", author="Test Author")
    book = book_repo.add(book)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/books/{book.id}/notes/999")
        assert response.status_code == 404
        data = response.json()
        assert (
            data["detail"] == "Note not found or doesn't belong to the specified book"
        )


@pytest.mark.asyncio
async def test_get_note_with_context_stream_wrong_book(
    setup_streaming_deps: StreamingDepsSetup,
):
    """Test streaming when note belongs to different book"""
    book_repo, note_repo, _, _ = setup_streaming_deps()

    # Create two books
    book1 = BookCreate(title="Book 1", author="Author 1")
    book2 = BookCreate(title="Book 2", author="Author 2")
    book1 = book_repo.add(book1)
    book2 = book_repo.add(book2)

    # Add note to book1
    note = NoteCreate(
        content="Note in book 1",
        content_hash="hash1",
        book_id=book1.id,
    )
    added_note = note_repo.add(note)

    # Try to access the note from book2 (should fail)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/books/{book2.id}/notes/{added_note.id}")
        assert response.status_code == 404
