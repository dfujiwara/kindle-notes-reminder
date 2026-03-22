"""
Search endpoints for semantic search across notes, URL chunks, and tweets using AI embeddings.
"""

import asyncio
from fastapi import APIRouter, Depends
from src.repositories.models import (
    BookResponse,
    NoteResponse,
    BookWithNoteResponses,
    NoteRead,
    URLResponse,
    URLChunkResponse,
    URLWithChunksResponses,
    URLChunkRead,
    SearchResult,
    TweetResponse,
    TweetThreadWithTweetsResponse,
    TweetRead,
)
from src.repositories.interfaces import (
    BookRepositoryInterface,
    NoteRepositoryInterface,
)
from src.url_ingestion.repositories.interfaces import (
    URLRepositoryInterface,
    URLChunkRepositoryInterface,
)
from src.tweet_ingestion.repositories.interfaces import (
    TweetThreadRepositoryInterface,
    TweetRepositoryInterface,
)
from src.embedding_interface import EmbeddingClientInterface
from src.dependencies import (
    get_book_repository,
    get_note_repository,
    get_url_repository,
    get_urlchunk_repository,
    get_embedding_client,
    get_tweet_thread_repository,
    get_tweet_repository,
)

router = APIRouter(tags=["search"])


def _group_and_fetch_notes(
    similar_notes: list[NoteRead],
    book_repository: BookRepositoryInterface,
) -> list[BookWithNoteResponses]:
    """
    Group notes by book and fetch related book information.

    Args:
        similar_notes: List of similar notes from embedding search
        book_repository: Repository for fetching book details

    Returns:
        List of BookWithNoteResponses grouped by book
    """
    books_dict: dict[int, BookWithNoteResponses] = {}

    if not similar_notes:
        return []

    book_ids = list({n.book_id for n in similar_notes})
    fetched_books = book_repository.get_by_ids(book_ids)
    fetched_books_dict = {b.id: b for b in fetched_books}

    for note in similar_notes:
        book_id = note.book_id
        if book_id not in books_dict:
            fetched_book = fetched_books_dict[book_id]
            book_response = BookResponse(
                id=note.book_id,
                title=fetched_book.title,
                author=fetched_book.author,
                created_at=fetched_book.created_at,
            )
            books_dict[book_id] = BookWithNoteResponses(book=book_response, notes=[])
        books_dict[book_id].notes.append(
            NoteResponse(id=note.id, content=note.content, created_at=note.created_at)
        )

    return list(books_dict.values())


def _group_and_fetch_chunks(
    similar_chunks: list[URLChunkRead],
    url_repository: URLRepositoryInterface,
) -> list[URLWithChunksResponses]:
    """
    Group URL chunks by URL and fetch related URL information.

    Args:
        similar_chunks: List of similar chunks from embedding search
        url_repository: Repository for fetching URL details

    Returns:
        List of URLWithChunksResponses grouped by URL
    """
    urls_dict: dict[int, URLWithChunksResponses] = {}

    if not similar_chunks:
        return []

    url_ids = list({c.url_id for c in similar_chunks})
    fetched_urls = url_repository.get_by_ids(url_ids)
    fetched_urls_dict = {u.id: u for u in fetched_urls}

    for chunk in similar_chunks:
        url_id = chunk.url_id
        if url_id not in urls_dict:
            fetched_url = fetched_urls_dict[url_id]
            url_response = URLResponse(
                id=url_id,
                url=fetched_url.url,
                title=fetched_url.title,
                fetched_at=fetched_url.fetched_at,
                created_at=fetched_url.created_at,
            )
            urls_dict[url_id] = URLWithChunksResponses(url=url_response, chunks=[])
        urls_dict[url_id].chunks.append(
            URLChunkResponse(
                id=chunk.id,
                content=chunk.content,
                chunk_order=chunk.chunk_order,
                is_summary=chunk.is_summary,
                created_at=chunk.created_at,
            )
        )

    return list(urls_dict.values())


def _group_and_fetch_tweets(
    similar_tweets: list[TweetRead],
    thread_repository: TweetThreadRepositoryInterface,
) -> list[TweetThreadWithTweetsResponse]:
    """
    Group matched tweets by thread and fetch related thread information.

    Args:
        similar_tweets: List of similar tweets from embedding search
        thread_repository: Repository for fetching thread details

    Returns:
        List of TweetThreadWithTweetsResponse grouped by thread, with only matched tweets
    """
    if not similar_tweets:
        return []

    thread_ids = list({t.thread_id for t in similar_tweets})
    fetched_threads = thread_repository.get_by_ids(thread_ids)
    fetched_threads_dict = {t.id: t for t in fetched_threads}

    threads_dict: dict[int, TweetThreadWithTweetsResponse] = {}
    for tweet in similar_tweets:
        thread_id = tweet.thread_id
        if thread_id not in threads_dict:
            thread = fetched_threads_dict[thread_id]
            threads_dict[thread_id] = TweetThreadWithTweetsResponse(
                thread=thread,
                tweets=[],
            )
        threads_dict[thread_id].tweets.append(
            TweetResponse(
                id=tweet.id,
                tweet_id=tweet.tweet_id,
                author_username=tweet.author_username,
                author_display_name=tweet.author_display_name,
                content=tweet.content,
                media_urls=tweet.media_urls,
                position_in_thread=tweet.position_in_thread,
                tweeted_at=tweet.tweeted_at,
                created_at=tweet.created_at,
            )
        )

    return list(threads_dict.values())


@router.get(
    "/search",
    summary="Semantic search across notes, URL content, and tweets",
    description="""
    Search for notes, URL content, and tweets using semantic search based on the provided query.

    This endpoint:
    - Converts your search query into embeddings using OpenAI
    - Finds semantically similar notes, URL chunks, and tweets using vector similarity
    - Groups notes by book, chunks by URL, and tweets by thread for better organization
    - Returns results with similarity scores above the threshold
    """,
    response_description="Search results grouped by book/URL/thread with similarity scores",
    responses={
        200: {"description": "Search completed successfully"},
        422: {"description": "Invalid query parameters"},
    },
)
async def search(
    q: str,
    limit: int = 10,
    book_repository: BookRepositoryInterface = Depends(get_book_repository),
    note_repository: NoteRepositoryInterface = Depends(get_note_repository),
    url_repository: URLRepositoryInterface = Depends(get_url_repository),
    urlchunk_repository: URLChunkRepositoryInterface = Depends(get_urlchunk_repository),
    tweet_thread_repository: TweetThreadRepositoryInterface = Depends(
        get_tweet_thread_repository
    ),
    tweet_repository: TweetRepositoryInterface = Depends(get_tweet_repository),
    embedding_client: EmbeddingClientInterface = Depends(get_embedding_client),
) -> SearchResult:
    # Validate limit
    limit = min(limit, 50)

    # Generate embedding for the search query
    query_embedding = await embedding_client.generate_embedding(q)

    # Search repositories sequentially to avoid session concurrency issues
    # All are already ordered by relevance (similarity score ascending)
    similar_notes = await asyncio.to_thread(
        note_repository.search_notes_by_embedding,
        query_embedding,
        limit,
        0.7,
    )
    similar_chunks = await asyncio.to_thread(
        urlchunk_repository.search_chunks_by_embedding,
        query_embedding,
        limit,
        0.7,
    )
    similar_tweets = await asyncio.to_thread(
        tweet_repository.search_tweets_by_embedding,
        query_embedding,
        limit,
        0.7,
    )

    # Group and fetch related data
    books_results = _group_and_fetch_notes(similar_notes, book_repository)
    urls_results = _group_and_fetch_chunks(similar_chunks, url_repository)
    tweet_threads_results = _group_and_fetch_tweets(
        similar_tweets, tweet_thread_repository
    )

    # Calculate total count
    total_count = (
        sum(len(book.notes) for book in books_results)
        + sum(len(url.chunks) for url in urls_results)
        + sum(len(thread.tweets) for thread in tweet_threads_results)
    )

    return SearchResult(
        query=q,
        results=books_results,  # For backwards compatibility
        books=books_results,
        urls=urls_results,
        tweet_threads=tweet_threads_results,
        count=total_count,
    )
