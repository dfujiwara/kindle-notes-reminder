"""
Test utilities and stub implementations for testing.

This module provides reusable stub implementations of repositories and clients
that can be used across different test files.
"""

from src.repositories.models import (
    BookCreate,
    BookResponse,
    NoteCreate,
    NoteRead,
    Evaluation,
    URLCreate,
    URLResponse,
    URLChunkCreate,
    URLChunkRead,
    TweetThreadCreate,
    TweetThreadResponse,
    TweetCreate,
    TweetRead,
)
from src.repositories.interfaces import (
    BookRepositoryInterface,
    EvaluationRepositoryInterface,
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
from src.embedding_interface import EmbeddingClientInterface, EmbeddingError
from src.llm_interface import LLMClientInterface, LLMError
from src.url_ingestion.url_fetcher import (
    URLFetchError,
    FetchedContent,
)
from src.tweet_ingestion.interfaces import (
    FetchedThread,
    FetchedTweet,
    TwitterFetchError,
)
from src.types import Embedding
from src.config import settings
from datetime import datetime, timezone
from typing import AsyncGenerator


class StubBookRepository(BookRepositoryInterface):
    """Stub implementation of BookRepository for testing."""

    def __init__(self, include_sample_book: bool = False):
        self.books: list[BookResponse] = []
        if include_sample_book:
            sample_book = BookResponse(
                id=1,
                title="The Pragmatic Programmer",
                author="David Thomas",
                created_at=datetime.now(timezone.utc),
            )
            self.books.append(sample_book)

    def add(self, book: BookCreate) -> BookResponse:
        book_read = BookResponse(
            id=len(self.books) + 1,
            title=book.title,
            author=book.author,
            created_at=datetime.now(timezone.utc),
        )
        self.books.append(book_read)
        return book_read

    def get(self, book_id: int) -> BookResponse | None:
        return next((book for book in self.books if book.id == book_id), None)

    def list_books(self) -> list[BookResponse]:
        return self.books

    def get_by_ids(self, book_ids: list[int]) -> list[BookResponse]:
        return [b for b in self.books if b.id in book_ids]

    def delete(self, book_id: int) -> None:
        self.books = [book for book in self.books if book.id != book_id]


class StubNoteRepository(NoteRepositoryInterface):
    """Stub implementation of NoteRepository for testing."""

    def __init__(self):
        self.notes: list[NoteRead] = []

    def add(self, note: NoteCreate) -> NoteRead:
        note_read = NoteRead(
            id=len(self.notes) + 1,
            created_at=datetime.now(timezone.utc),
            book_id=note.book_id,
            content=note.content,
            content_hash=note.content_hash,
            embedding=note.embedding,
        )
        self.notes.append(note_read)
        return note_read

    def get(self, note_id: int, book_id: int) -> NoteRead | None:
        return next(
            (
                note
                for note in self.notes
                if note.id == note_id and note.book_id == book_id
            ),
            None,
        )

    def get_by_id(self, note_id: int) -> NoteRead | None:
        return next((note for note in self.notes if note.id == note_id), None)

    def list_notes(self) -> list[NoteRead]:
        return self.notes

    def get_by_book_id(self, book_id: int) -> list[NoteRead]:
        return [note for note in self.notes if note.book_id == book_id]

    def delete(self, note_id: int) -> None:
        self.notes = [note for note in self.notes if note.id != note_id]

    def get_random(self) -> NoteRead | None:
        return self.notes[0] if self.notes else None

    def find_similar_notes(
        self, note: NoteRead, limit: int = 5, similarity_threshold: float = 0.3
    ) -> list[NoteRead]:
        """
        Stub implementation of find_similar_notes.
        Returns first `limit` notes from the same book (excluding the input note).
        """
        return [n for n in self.notes if n.id != note.id and n.book_id == note.book_id][
            :limit
        ]

    def search_notes_by_embedding(
        self, embedding: Embedding, limit: int = 10, similarity_threshold: float = 0.5
    ) -> list[NoteRead]:
        """
        Stub implementation of search_notes_by_embedding.
        Returns first `limit` notes from all books.
        """
        return self.notes[:limit]

    def get_note_counts_by_book_ids(self, book_ids: list[int]) -> dict[int, int]:
        result: dict[int, int] = {}
        book_ids_set = set(book_ids)
        for note in self.notes:
            if note.book_id in book_ids_set:
                result[note.book_id] = result.get(note.book_id, 0) + 1
        return result

    def count_with_embeddings(self) -> int:
        return len([n for n in self.notes if n.embedding is not None])

    def delete_by_book_id(self, book_id: int) -> None:
        self.notes = [n for n in self.notes if n.book_id != book_id]


class StubEvaluationRepository(EvaluationRepositoryInterface):
    def __init__(self):
        self.evaluations: list[Evaluation] = []

    def add(self, evaluation: Evaluation) -> Evaluation:
        evaluation.id = len(self.evaluations) + 1  # Simulate auto-increment ID
        self.evaluations.append(evaluation)
        return evaluation

    def get_by_note_id(self, note_id: int) -> list[Evaluation]:
        return [eval for eval in self.evaluations if eval.note_id == note_id]

    def delete_by_note_ids(self, note_ids: list[int]) -> None:
        note_ids_set = set(note_ids)
        self.evaluations = [
            e for e in self.evaluations if e.note_id not in note_ids_set
        ]


class StubURLRepository(URLRepositoryInterface):
    """Stub implementation of URLRepository for testing."""

    def __init__(self):
        self.urls: list[URLResponse] = []

    def add(self, url: URLCreate) -> URLResponse:
        # Check for duplicate URL
        existing = self.get_by_url(url.url)
        if existing:
            return existing

        url_response = URLResponse(
            id=len(self.urls) + 1,
            url=url.url,
            title=url.title,
            fetched_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        self.urls.append(url_response)
        return url_response

    def get(self, url_id: int) -> URLResponse | None:
        return next((url for url in self.urls if url.id == url_id), None)

    def get_by_url(self, url: str) -> URLResponse | None:
        return next((u for u in self.urls if u.url == url), None)

    def get_by_ids(self, url_ids: list[int]) -> list[URLResponse]:
        return [url for url in self.urls if url.id in url_ids]

    def list_urls(self) -> list[URLResponse]:
        return self.urls

    def delete(self, url_id: int) -> None:
        self.urls = [url for url in self.urls if url.id != url_id]


class StubURLChunkRepository(URLChunkRepositoryInterface):
    """Stub implementation of URLChunkRepository for testing."""

    def __init__(self):
        self.chunks: list[URLChunkRead] = []

    def add(self, chunk: URLChunkCreate) -> URLChunkRead:
        # Check for duplicate content_hash
        existing = next(
            (c for c in self.chunks if c.content_hash == chunk.content_hash), None
        )
        if existing:
            return existing

        chunk_read = URLChunkRead(
            id=len(self.chunks) + 1,
            created_at=datetime.now(timezone.utc),
            url_id=chunk.url_id,
            content=chunk.content,
            content_hash=chunk.content_hash,
            chunk_order=chunk.chunk_order,
            is_summary=chunk.is_summary,
            embedding=chunk.embedding,
        )
        self.chunks.append(chunk_read)
        return chunk_read

    def get(self, chunk_id: int, url_id: int) -> URLChunkRead | None:
        return next(
            (
                chunk
                for chunk in self.chunks
                if chunk.id == chunk_id and chunk.url_id == url_id
            ),
            None,
        )

    def get_by_id(self, chunk_id: int) -> URLChunkRead | None:
        return next((chunk for chunk in self.chunks if chunk.id == chunk_id), None)

    def get_random(self) -> URLChunkRead | None:
        return self.chunks[0] if self.chunks else None

    def get_by_url_id(self, url_id: int) -> list[URLChunkRead]:
        return sorted(
            [chunk for chunk in self.chunks if chunk.url_id == url_id],
            key=lambda c: c.chunk_order,
        )

    def find_similar_chunks(
        self, chunk: URLChunkRead, limit: int = 5
    ) -> list[URLChunkRead]:
        """
        Stub implementation of find_similar_chunks.
        Returns first `limit` chunks from the same URL (excluding the input chunk).
        """
        return [
            c for c in self.chunks if c.id != chunk.id and c.url_id == chunk.url_id
        ][:limit]

    def search_chunks_by_embedding(
        self, embedding: Embedding, limit: int = 10, similarity_threshold: float = 0.5
    ) -> list[URLChunkRead]:
        """
        Stub implementation of search_chunks_by_embedding.
        Returns first `limit` chunks from all URLs.
        """
        return self.chunks[:limit]

    def get_chunk_counts_by_url_ids(self, url_ids: list[int]) -> dict[int, int]:
        result: dict[int, int] = {}
        url_ids_set = set(url_ids)
        for chunk in self.chunks:
            if chunk.url_id in url_ids_set:
                result[chunk.url_id] = result.get(chunk.url_id, 0) + 1
        return result

    def count_with_embeddings(self) -> int:
        return len([c for c in self.chunks if c.embedding is not None])

    def delete_by_url_id(self, url_id: int) -> None:
        self.chunks = [c for c in self.chunks if c.url_id != url_id]


class StubURLFetcher:
    """Stub implementation of URL fetcher for testing."""

    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail
        self.calls: list[str] = []  # Track calls for verification

    async def __call__(
        self, url: str, max_content_size: int | None = None
    ) -> FetchedContent:
        """Stub fetcher that can simulate success or failure."""
        self.calls.append(url)

        if self.should_fail:
            raise URLFetchError("Failed to fetch URL")

        # Return minimal successful fetch result
        return FetchedContent(
            url=url, title=f"Test: {url}", content="Test content from stub fetcher."
        )


class StubEmbeddingClient(EmbeddingClientInterface):
    """Stub implementation of EmbeddingClient for testing."""

    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail

    async def generate_embedding(self, content: str) -> list[float]:
        if self.should_fail:
            raise EmbeddingError("Simulated embedding generation failure")
        # Return a simple mock embedding using configured dimension
        return [0.1] * settings.embedding_dimension


class StubLLMClient(LLMClientInterface):
    """Stub implementation of LLMClient for testing."""

    def __init__(
        self, responses: list[str] = ["Test LLM response"], should_fail: bool = False
    ):
        self.responses = responses
        self.should_fail = should_fail
        self.call_count = 0

    async def get_response(
        self, prompt: str, instruction: str, json_mode: bool = False
    ) -> str:
        response = self.responses[self.call_count]
        self.call_count += 1
        if self.should_fail:
            raise LLMError("Simulated LLM generation failure")
        return response

    async def get_response_stream(
        self, prompt: str, instruction: str
    ) -> AsyncGenerator[str, None]:
        """Stub streaming response that yields the response in chunks."""
        response = await self.get_response(prompt, instruction)
        # Yield response in chunks (simulate streaming)
        chunk_size = 10
        for i in range(0, len(response), chunk_size):
            yield response[i : i + chunk_size]


class StubTwitterFetcher:
    """Stub implementation of Twitter thread fetcher for testing."""

    def __init__(
        self,
        should_fail: bool = False,
        root_tweet_id: str = "tweet123",
    ):
        self.should_fail = should_fail
        self.root_tweet_id = root_tweet_id
        self.calls: list[str] = []

    async def __call__(self, tweet_id: str, max_depth: int = 50) -> FetchedThread:
        self.calls.append(tweet_id)
        if self.should_fail:
            raise TwitterFetchError("Failed to fetch tweet")
        fetched_tweet = FetchedTweet(
            tweet_id=tweet_id,
            author_username="test_user",
            author_display_name="Test User",
            content="Test tweet content",
            tweeted_at=datetime.now(timezone.utc),
        )
        return FetchedThread(
            root_tweet_id=self.root_tweet_id,
            author_username="test_user",
            author_display_name="Test User",
            tweets=[fetched_tweet],
        )


class StubTweetThreadRepository(TweetThreadRepositoryInterface):
    """Stub implementation of TweetThreadRepository for testing."""

    def __init__(self):
        self.threads: list[TweetThreadResponse] = []

    def add(self, thread: TweetThreadCreate) -> TweetThreadResponse:
        # Check for duplicate root_tweet_id
        existing = self.get_by_root_tweet_id(thread.root_tweet_id)
        if existing:
            return existing

        thread_response = TweetThreadResponse(
            id=len(self.threads) + 1,
            root_tweet_id=thread.root_tweet_id,
            author_username=thread.author_username,
            author_display_name=thread.author_display_name,
            title=thread.title,
            tweet_count=0,
            fetched_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        self.threads.append(thread_response)
        return thread_response

    def get(self, id: int) -> TweetThreadResponse | None:
        return next((t for t in self.threads if t.id == id), None)

    def get_by_root_tweet_id(self, root_tweet_id: str) -> TweetThreadResponse | None:
        return next((t for t in self.threads if t.root_tweet_id == root_tweet_id), None)

    def get_by_ids(self, ids: list[int]) -> list[TweetThreadResponse]:
        return [t for t in self.threads if t.id in ids]

    def list_threads(self) -> list[TweetThreadResponse]:
        return self.threads

    def update_tweet_count(self, thread_id: int, tweet_count: int) -> None:
        for thread in self.threads:
            if thread.id == thread_id:
                # Create a new response with updated tweet_count
                idx = self.threads.index(thread)
                self.threads[idx] = TweetThreadResponse(
                    id=thread.id,
                    root_tweet_id=thread.root_tweet_id,
                    author_username=thread.author_username,
                    author_display_name=thread.author_display_name,
                    title=thread.title,
                    tweet_count=tweet_count,
                    fetched_at=thread.fetched_at,
                    created_at=thread.created_at,
                )
                break

    def delete(self, thread_id: int) -> None:
        self.threads = [t for t in self.threads if t.id != thread_id]


class StubTweetRepository(TweetRepositoryInterface):
    """Stub implementation of TweetRepository for testing."""

    def __init__(self):
        self.tweets: list[TweetRead] = []

    def add(self, tweet: TweetCreate) -> TweetRead:
        # Check for duplicate tweet_id
        existing = self.get_by_tweet_id(tweet.tweet_id)
        if existing:
            return existing

        tweet_read = TweetRead(
            id=len(self.tweets) + 1,
            tweet_id=tweet.tweet_id,
            author_username=tweet.author_username,
            author_display_name=tweet.author_display_name,
            content=tweet.content,
            media_urls=tweet.media_urls,
            thread_id=tweet.thread_id,
            position_in_thread=tweet.position_in_thread,
            tweeted_at=tweet.tweeted_at,
            embedding=tweet.embedding,
            created_at=datetime.now(timezone.utc),
        )
        self.tweets.append(tweet_read)
        return tweet_read

    def get(self, id: int, thread_id: int) -> TweetRead | None:
        return next(
            (t for t in self.tweets if t.id == id and t.thread_id == thread_id),
            None,
        )

    def get_by_id(self, id: int) -> TweetRead | None:
        return next((t for t in self.tweets if t.id == id), None)

    def get_by_tweet_id(self, tweet_id: str) -> TweetRead | None:
        return next((t for t in self.tweets if t.tweet_id == tweet_id), None)

    def get_random(self) -> TweetRead | None:
        return self.tweets[0] if self.tweets else None

    def get_by_thread_id(self, thread_id: int) -> list[TweetRead]:
        return sorted(
            [t for t in self.tweets if t.thread_id == thread_id],
            key=lambda t: t.position_in_thread,
        )

    def find_similar_tweets(self, tweet: TweetRead, limit: int = 5) -> list[TweetRead]:
        """Stub: returns first `limit` tweets from same thread (excluding input)."""
        return [
            t
            for t in self.tweets
            if t.id != tweet.id and t.thread_id == tweet.thread_id
        ][:limit]

    def search_tweets_by_embedding(
        self, embedding: Embedding, limit: int = 10, similarity_threshold: float = 0.5
    ) -> list[TweetRead]:
        """Stub: returns first `limit` tweets."""
        return self.tweets[:limit]

    def get_tweet_counts_by_thread_ids(self, thread_ids: list[int]) -> dict[int, int]:
        result: dict[int, int] = {}
        thread_ids_set = set(thread_ids)
        for tweet in self.tweets:
            if tweet.thread_id in thread_ids_set:
                result[tweet.thread_id] = result.get(tweet.thread_id, 0) + 1
        return result

    def count_with_embeddings(self) -> int:
        return len([t for t in self.tweets if t.embedding is not None])


def make_thread_and_tweets(
    thread_repo: StubTweetThreadRepository,
    tweet_repo: StubTweetRepository,
    tweet_contents: list[str],
    root_tweet_id: str = "thread1",
    embedding: list[float] | None = None,
) -> tuple[TweetThreadResponse, list[TweetRead]]:
    """Create a thread and its tweets in stub repos. Returns (thread, tweets)."""
    if embedding is None:
        embedding = [0.5] * 1536

    thread = thread_repo.add(
        TweetThreadCreate(
            root_tweet_id=root_tweet_id,
            author_username="testuser",
            author_display_name="Test User",
            title=tweet_contents[0][:50],
        )
    )
    tweets: list[TweetRead] = []
    for i, content in enumerate(tweet_contents):
        tweet = tweet_repo.add(
            TweetCreate(
                tweet_id=f"{root_tweet_id}_{i}",
                author_username="testuser",
                author_display_name="Test User",
                content=content,
                media_urls=[],
                thread_id=thread.id,
                position_in_thread=i,
                embedding=embedding,
                tweeted_at=datetime.now(timezone.utc),
            )
        )
        tweets.append(tweet)
    return thread, tweets
