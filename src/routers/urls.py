"""
URL-related endpoints for ingesting and exploring URLs with AI enhancements.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import SQLModel, Field
from pydantic import HttpUrl
from src.url_ingestion.repositories.interfaces import (
    URLRepositoryInterface,
    URLChunkRepositoryInterface,
)
from src.llm_interface import LLMClientInterface
from src.embedding_interface import EmbeddingClientInterface
from src.url_ingestion.url_fetcher import URLFetcherInterface
from src.dependencies import (
    get_url_repository,
    get_urlchunk_repository,
    get_llm_client,
    get_embedding_client,
    get_url_fetcher,
)
from src.repositories.models import (
    URLWithChunksResponses,
    URLWithChunksResponse,
    URLChunkResponse,
)
from src.url_ingestion.url_processor import process_url_content
from src.url_ingestion.url_fetcher import URLFetchError
from src.prompts import create_chunk_context_prompt, SYSTEM_INSTRUCTIONS
from src.context_generation.additional_context import get_additional_context_stream
from src.sse_utils import format_sse
from src.routers.response_builders import build_unified_response_for_chunk
from typing import AsyncGenerator
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["urls"])

# Constants
RELATED_CHUNKS_LIMIT = 3


class URLIngestRequest(SQLModel):
    """Request model for URL ingestion."""

    url: HttpUrl = Field(description="URL to ingest and process")


class URLListResponse(SQLModel):
    """Response model for listing URLs with chunk counts."""

    urls: list[URLWithChunksResponse]


@router.post(
    "/urls",
    summary="Ingest URL content",
    description="""
    Process and store URL content with chunking and embeddings.

    This endpoint:
    - Fetches content from the provided URL
    - Chunks the content into semantic sections
    - Generates AI summary and embeddings
    - Stores everything with deduplication

    If the URL has already been ingested, returns the existing record without re-fetching.
    """,
    response_description="Processing result with URL and chunks",
    responses={
        422: {"description": "Cannot process URL (fetch error or no content)"},
        200: {"description": "URL processed successfully"},
    },
)
async def ingest_url(
    request: URLIngestRequest,
    url_repository: URLRepositoryInterface = Depends(get_url_repository),
    chunk_repository: URLChunkRepositoryInterface = Depends(get_urlchunk_repository),
    llm_client: LLMClientInterface = Depends(get_llm_client),
    embedding_client: EmbeddingClientInterface = Depends(get_embedding_client),
    url_fetcher: URLFetcherInterface = Depends(get_url_fetcher),
) -> URLWithChunksResponses:
    """Ingest and process URL content."""
    url_str = str(request.url)
    try:
        result = await process_url_content(
            url_str,
            url_repository,
            chunk_repository,
            llm_client,
            embedding_client,
            fetch_fn=url_fetcher,
        )
        return result
    except URLFetchError as e:
        logger.error(f"URL fetch error for {url_str}: {str(e)}")
        raise HTTPException(status_code=422, detail=f"Cannot process URL: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error processing URL {url_str}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing URL: {str(e)}")


@router.get(
    "/urls",
    summary="List all URLs",
    description="Retrieve all processed URLs with their chunk counts",
    response_description="List of URLs with metadata and chunk counts",
    response_model=URLListResponse,
)
async def get_urls(
    url_repository: URLRepositoryInterface = Depends(get_url_repository),
    chunk_repository: URLChunkRepositoryInterface = Depends(get_urlchunk_repository),
) -> URLListResponse:
    """List all URLs with chunk counts."""
    urls = url_repository.list_urls()
    chunk_count_dict = chunk_repository.get_chunk_counts_by_url_ids(
        [u.id for u in urls]
    )

    url_responses: list[URLWithChunksResponse] = []
    for url in urls:
        url_responses.append(
            URLWithChunksResponse(
                id=url.id,
                url=url.url,
                title=url.title,
                fetched_at=url.fetched_at,
                created_at=url.created_at,
                chunk_count=chunk_count_dict.get(url.id, 0),
            )
        )

    return URLListResponse(urls=url_responses)


@router.delete(
    "/urls/{url_id}",
    summary="Delete URL and all its chunks",
    description="Delete a URL and all associated chunks from the database.",
    status_code=204,
    responses={
        404: {"description": "URL not found"},
        204: {"description": "URL deleted successfully"},
    },
)
async def delete_url(
    url_id: int,
    url_repository: URLRepositoryInterface = Depends(get_url_repository),
    chunk_repository: URLChunkRepositoryInterface = Depends(get_urlchunk_repository),
) -> None:
    """Delete a URL and all its chunks."""
    url = url_repository.get(url_id)
    if not url:
        raise HTTPException(status_code=404, detail="URL not found")

    chunk_repository.delete_by_url_id(url_id)
    url_repository.delete(url_id)


@router.get(
    "/urls/{url_id}",
    summary="Get URL with all chunks",
    description="Retrieve URL metadata and all chunks ordered by chunk_order",
    response_description="URL metadata with all chunks",
    response_model=URLWithChunksResponses,
    responses={
        404: {"description": "URL not found"},
        200: {"description": "URL retrieved successfully"},
    },
)
async def get_url_with_chunks(
    url_id: int,
    url_repository: URLRepositoryInterface = Depends(get_url_repository),
    chunk_repository: URLChunkRepositoryInterface = Depends(get_urlchunk_repository),
) -> URLWithChunksResponses:
    """Get URL with all chunks."""
    url = url_repository.get(url_id)
    if not url:
        raise HTTPException(status_code=404, detail="URL not found")

    chunk_reads = chunk_repository.get_by_url_id(url_id)
    chunks = [URLChunkResponse.model_validate(chunk) for chunk in chunk_reads]
    return URLWithChunksResponses(url=url, chunks=chunks)


@router.get(
    "/urls/{url_id}/chunks/{chunk_id}",
    summary="Get URL chunk with streaming AI context",
    description="""
    Retrieve a specific URL chunk with AI-generated context streamed as Server-Sent Events.

    This endpoint:
    - Returns chunk content and metadata
    - Streams AI-powered additional context using SSE
    - Finds related chunks based on vector similarity

    **SSE Events:**
    - `metadata`: Contains URL, chunk, and related_chunks data
    - `context_chunk`: Chunks of AI-generated context as they arrive
    - `context_complete`: Signals the end of streaming
    - `error`: Error information if something goes wrong
    """,
    response_class=StreamingResponse,
)
async def get_chunk_with_context_stream(
    url_id: int,
    chunk_id: int,
    background_tasks: BackgroundTasks,
    url_repository: URLRepositoryInterface = Depends(get_url_repository),
    chunk_repository: URLChunkRepositoryInterface = Depends(get_urlchunk_repository),
    llm_client: LLMClientInterface = Depends(get_llm_client),
) -> StreamingResponse:
    """Stream a URL chunk with AI-generated context."""

    # Fetch and validate data before streaming
    chunk = chunk_repository.get(chunk_id=chunk_id, url_id=url_id)
    if not chunk:
        logger.error(f"Error finding chunk with id {chunk_id} in URL {url_id}")
        raise HTTPException(
            status_code=404,
            detail="Chunk not found or doesn't belong to the specified URL",
        )

    url = url_repository.get(url_id)
    if not url:
        logger.error(f"Error finding URL with id {url_id}")
        raise HTTPException(status_code=404, detail="URL not found")

    # Find similar chunks (from same URL)
    similar_chunks = chunk_repository.find_similar_chunks(
        chunk, limit=RELATED_CHUNKS_LIMIT
    )

    # Prepare metadata using unified response builder
    metadata = build_unified_response_for_chunk(url, chunk, similar_chunks)

    async def event_generator() -> AsyncGenerator[str, None]:
        # Send metadata first
        yield format_sse("metadata", metadata.model_dump(mode="json"))

        # Prepare prompt and instruction
        prompt = create_chunk_context_prompt(
            url.url,
            url.title,
            chunk.content,
            [similar_chunk.content for similar_chunk in similar_chunks],
        )
        instruction = SYSTEM_INSTRUCTIONS["context_provider"]

        # Stream context chunks
        try:
            async for stream_chunk in get_additional_context_stream(
                llm_client, prompt, instruction
            ):
                if not stream_chunk.is_complete:
                    # It's a content chunk
                    yield format_sse("context_chunk", {"content": stream_chunk.content})
        except Exception as e:
            logger.error(f"Error streaming context: {e}")
            yield format_sse("error", {"detail": str(e)})
            return

        # Signal completion
        yield format_sse("context_complete", {})

        # NOTE: We do NOT add background evaluation for URL chunks
        # Evaluation is only for Kindle notes (note_id in Evaluation model)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
