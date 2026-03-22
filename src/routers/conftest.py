"""
Shared pytest fixtures for router tests.

Provides reusable dependency injection fixtures to reduce boilerplate in test files.
Each fixture returns a setup function that creates fresh stub repositories and
dependency overrides with automatic cleanup.
"""

from typing import Generator, Callable
import pytest

from src.main import app
from src.dependencies import (
    get_book_repository,
    get_note_repository,
    get_evaluation_repository,
    get_embedding_client,
    get_llm_client,
    get_url_repository,
    get_urlchunk_repository,
    get_url_fetcher,
    get_tweet_thread_repository,
    get_tweet_repository,
    get_twitter_fetcher,
    get_session_factory,
)
from src.database import SessionFactory
from src.test_utils import (
    StubBookRepository,
    StubNoteRepository,
    StubEvaluationRepository,
    StubEmbeddingClient,
    StubLLMClient,
    StubURLRepository,
    StubURLChunkRepository,
    StubURLFetcher,
    StubTweetThreadRepository,
    StubTweetRepository,
    StubTwitterFetcher,
)

# Type aliases for fixtures
BookNoteDepsSetup = Callable[..., tuple[StubBookRepository, StubNoteRepository]]
SearchDepsSetup = Callable[
    ...,
    tuple[
        StubBookRepository,
        StubNoteRepository,
        StubEmbeddingClient,
        StubURLRepository,
        StubURLChunkRepository,
        StubTweetThreadRepository,
        StubTweetRepository,
    ],
]
EvaluationDepsSetup = Callable[..., tuple[StubNoteRepository, StubEvaluationRepository]]
URLDepsSetup = Callable[
    ...,
    tuple[
        StubURLRepository,
        StubURLChunkRepository,
        StubURLFetcher,
    ],
]
NotebookDepsSetup = Callable[
    ...,
    tuple[StubBookRepository, StubNoteRepository, StubEmbeddingClient, StubLLMClient],
]
BookDeleteDepsSetup = Callable[
    ...,
    tuple[StubBookRepository, StubNoteRepository, StubEvaluationRepository],
]
RandomV2DepsSetup = Callable[
    ...,
    tuple[
        StubBookRepository,
        StubNoteRepository,
        StubEvaluationRepository,
        StubURLRepository,
        StubURLChunkRepository,
        StubTweetThreadRepository,
        StubTweetRepository,
    ],
]
StreamingDepsSetup = Callable[
    ...,
    tuple[
        StubBookRepository,
        StubNoteRepository,
        StubEvaluationRepository,
        StubLLMClient,
    ],
]
TweetDepsSetup = Callable[
    ...,
    tuple[
        StubTweetThreadRepository,
        StubTweetRepository,
        StubTwitterFetcher,
    ],
]


@pytest.fixture
def setup_book_note_deps() -> Generator[BookNoteDepsSetup, None, None]:
    """
    Setup book and note repository dependencies.

    Returns a function for flexible configuration. Most common pattern - used in ~15 tests.

    Usage:
        def test_something(setup_book_note_deps):
            book_repo, note_repo = setup_book_note_deps()
            # or with config:
            book_repo, note_repo = setup_book_note_deps(include_sample_book=True)
            # Cleanup is automatic!
    """

    def _setup(
        include_sample_book: bool = False,
    ) -> tuple[StubBookRepository, StubNoteRepository]:
        book_repo = StubBookRepository(include_sample_book=include_sample_book)
        note_repo = StubNoteRepository()

        app.dependency_overrides[get_book_repository] = lambda: book_repo
        app.dependency_overrides[get_note_repository] = lambda: note_repo

        return book_repo, note_repo

    yield _setup
    app.dependency_overrides.clear()


@pytest.fixture
def setup_search_deps() -> Generator[SearchDepsSetup, None, None]:
    """
    Setup dependencies for search endpoints (book, note, URL, tweet, embedding).

    Returns a function for flexible configuration. Used in test_search.py.

    Usage:
        def test_search(setup_search_deps):
            book_repo, note_repo, embedding_client, url_repo, chunk_repo, thread_repo, tweet_repo = setup_search_deps()
            # or with config:
            book_repo, note_repo, embedding_client, url_repo, chunk_repo, thread_repo, tweet_repo = setup_search_deps(embedding_should_fail=True)
            # Cleanup is automatic!
    """

    def _setup(
        embedding_should_fail: bool = False,
    ) -> tuple[
        StubBookRepository,
        StubNoteRepository,
        StubEmbeddingClient,
        StubURLRepository,
        StubURLChunkRepository,
        StubTweetThreadRepository,
        StubTweetRepository,
    ]:
        book_repo = StubBookRepository()
        note_repo = StubNoteRepository()
        embedding_client = StubEmbeddingClient(should_fail=embedding_should_fail)
        url_repo = StubURLRepository()
        chunk_repo = StubURLChunkRepository()
        thread_repo = StubTweetThreadRepository()
        tweet_repo = StubTweetRepository()

        app.dependency_overrides[get_book_repository] = lambda: book_repo
        app.dependency_overrides[get_note_repository] = lambda: note_repo
        app.dependency_overrides[get_embedding_client] = lambda: embedding_client
        app.dependency_overrides[get_url_repository] = lambda: url_repo
        app.dependency_overrides[get_urlchunk_repository] = lambda: chunk_repo
        app.dependency_overrides[get_tweet_thread_repository] = lambda: thread_repo
        app.dependency_overrides[get_tweet_repository] = lambda: tweet_repo

        return (
            book_repo,
            note_repo,
            embedding_client,
            url_repo,
            chunk_repo,
            thread_repo,
            tweet_repo,
        )

    yield _setup
    app.dependency_overrides.clear()


@pytest.fixture
def setup_evaluation_deps() -> Generator[EvaluationDepsSetup, None, None]:
    """
    Setup dependencies for evaluation endpoints (note, evaluation).

    Returns a function for consistent interface. Used in test_evaluations.py (3 tests).

    Usage:
        def test_evaluation(setup_evaluation_deps):
            note_repo, eval_repo = setup_evaluation_deps()
            # Cleanup is automatic!
    """

    def _setup() -> tuple[StubNoteRepository, StubEvaluationRepository]:
        note_repo = StubNoteRepository()
        eval_repo = StubEvaluationRepository()

        app.dependency_overrides[get_note_repository] = lambda: note_repo
        app.dependency_overrides[get_evaluation_repository] = lambda: eval_repo

        return note_repo, eval_repo

    yield _setup
    app.dependency_overrides.clear()


@pytest.fixture
def setup_book_delete_deps() -> Generator[BookDeleteDepsSetup, None, None]:
    """Setup dependencies for book delete endpoint (book, note, evaluation)."""

    def _setup() -> tuple[
        StubBookRepository, StubNoteRepository, StubEvaluationRepository
    ]:
        book_repo = StubBookRepository()
        note_repo = StubNoteRepository()
        eval_repo = StubEvaluationRepository()

        app.dependency_overrides[get_book_repository] = lambda: book_repo
        app.dependency_overrides[get_note_repository] = lambda: note_repo
        app.dependency_overrides[get_evaluation_repository] = lambda: eval_repo

        return book_repo, note_repo, eval_repo

    yield _setup
    app.dependency_overrides.clear()


@pytest.fixture
def setup_url_deps() -> Generator[URLDepsSetup, None, None]:
    """
    Setup dependencies for URL endpoints.

    Returns a function for flexible configuration. Used in test_urls.py (5 tests).

    Usage:
        def test_url(setup_url_deps):
            url_repo, chunk_repo, fetcher = setup_url_deps()
            # or with error simulation:
            url_repo, chunk_repo, fetcher = setup_url_deps(fetcher_should_fail=True)
            # Cleanup is automatic!
    """

    def _setup(
        fetcher_should_fail: bool = False,
    ) -> tuple[StubURLRepository, StubURLChunkRepository, StubURLFetcher]:
        url_repo = StubURLRepository()
        chunk_repo = StubURLChunkRepository()
        fetcher = StubURLFetcher(should_fail=fetcher_should_fail)

        app.dependency_overrides[get_url_repository] = lambda: url_repo
        app.dependency_overrides[get_urlchunk_repository] = lambda: chunk_repo
        app.dependency_overrides[get_url_fetcher] = lambda: fetcher
        app.dependency_overrides[get_llm_client] = lambda: StubLLMClient()
        app.dependency_overrides[get_embedding_client] = lambda: StubEmbeddingClient()

        return url_repo, chunk_repo, fetcher

    yield _setup
    app.dependency_overrides.clear()


@pytest.fixture
def setup_notebook_deps() -> Generator[NotebookDepsSetup, None, None]:
    """
    Setup dependencies for notebook upload endpoints.

    Returns a function for consistent interface. Used in test_notebooks.py (3 tests).

    Usage:
        def test_notebook(setup_notebook_deps):
            book_repo, note_repo, embedding_client, llm_client = setup_notebook_deps()
            # Cleanup is automatic!
    """

    def _setup() -> tuple[
        StubBookRepository, StubNoteRepository, StubEmbeddingClient, StubLLMClient
    ]:
        book_repo = StubBookRepository()
        note_repo = StubNoteRepository()
        embedding_client = StubEmbeddingClient()
        llm_client = StubLLMClient()

        app.dependency_overrides[get_book_repository] = lambda: book_repo
        app.dependency_overrides[get_note_repository] = lambda: note_repo
        app.dependency_overrides[get_embedding_client] = lambda: embedding_client
        app.dependency_overrides[get_llm_client] = lambda: llm_client

        return book_repo, note_repo, embedding_client, llm_client

    yield _setup
    app.dependency_overrides.clear()


@pytest.fixture
def setup_random_v2_deps(
    session_factory: SessionFactory,
) -> Generator[RandomV2DepsSetup, None, None]:
    """
    Setup dependencies for /random/v2 endpoint (book, note, evaluation, URL, chunk, tweet).

    Returns a function for consistent interface. Used in test_random_v2.py.

    Usage:
        def test_random_v2(setup_random_v2_deps):
            book_repo, note_repo, eval_repo, url_repo, chunk_repo, thread_repo, tweet_repo = setup_random_v2_deps()
            # Cleanup is automatic!
    """

    def _setup() -> tuple[
        StubBookRepository,
        StubNoteRepository,
        StubEvaluationRepository,
        StubURLRepository,
        StubURLChunkRepository,
        StubTweetThreadRepository,
        StubTweetRepository,
    ]:
        book_repo = StubBookRepository()
        note_repo = StubNoteRepository()
        eval_repo = StubEvaluationRepository()
        url_repo = StubURLRepository()
        chunk_repo = StubURLChunkRepository()
        thread_repo = StubTweetThreadRepository()
        tweet_repo = StubTweetRepository()

        app.dependency_overrides[get_book_repository] = lambda: book_repo
        app.dependency_overrides[get_note_repository] = lambda: note_repo
        app.dependency_overrides[get_evaluation_repository] = lambda: eval_repo
        app.dependency_overrides[get_url_repository] = lambda: url_repo
        app.dependency_overrides[get_urlchunk_repository] = lambda: chunk_repo
        app.dependency_overrides[get_tweet_thread_repository] = lambda: thread_repo
        app.dependency_overrides[get_tweet_repository] = lambda: tweet_repo
        app.dependency_overrides[get_session_factory] = lambda: session_factory
        # Provide multiple responses: one for context generation, one for evaluation
        app.dependency_overrides[get_llm_client] = lambda: StubLLMClient(
            responses=["Test LLM response", "Score: 0.8\nEvaluation: Good response"]
        )

        return (
            book_repo,
            note_repo,
            eval_repo,
            url_repo,
            chunk_repo,
            thread_repo,
            tweet_repo,
        )

    yield _setup
    app.dependency_overrides.clear()


@pytest.fixture
def setup_streaming_deps(
    session_factory: SessionFactory,
) -> Generator[StreamingDepsSetup, None, None]:
    """
    Setup dependencies for SSE streaming endpoints (book, note, evaluation, LLM).

    Accepts optional llm_responses to configure the stub LLM client.
    Used in test_streaming.py.

    Usage:
        def test_streaming(setup_streaming_deps):
            book_repo, note_repo, eval_repo, llm_client = setup_streaming_deps()
            # or with custom LLM responses:
            book_repo, note_repo, eval_repo, llm_client = setup_streaming_deps(
                llm_responses=["Context response", "Score: 0.9\\nEvaluation: Great"]
            )
            # Cleanup is automatic!
    """

    def _setup(
        llm_responses: list[str] | None = None,
    ) -> tuple[
        StubBookRepository,
        StubNoteRepository,
        StubEvaluationRepository,
        StubLLMClient,
    ]:
        if llm_responses is None:
            llm_responses = [
                "Default additional context",
                "Score: 0.85\nEvaluation: Well-structured and informative response.",
            ]

        book_repo = StubBookRepository()
        note_repo = StubNoteRepository()
        eval_repo = StubEvaluationRepository()
        llm_client = StubLLMClient(responses=llm_responses)

        app.dependency_overrides[get_book_repository] = lambda: book_repo
        app.dependency_overrides[get_note_repository] = lambda: note_repo
        app.dependency_overrides[get_evaluation_repository] = lambda: eval_repo
        app.dependency_overrides[get_llm_client] = lambda: llm_client
        app.dependency_overrides[get_session_factory] = lambda: session_factory

        return book_repo, note_repo, eval_repo, llm_client

    yield _setup
    app.dependency_overrides.clear()


@pytest.fixture
def setup_tweet_deps() -> Generator[TweetDepsSetup, None, None]:
    """
    Setup dependencies for tweet endpoints.

    Returns a function for flexible configuration. Used in test_tweets.py.

    Usage:
        def test_tweet(setup_tweet_deps):
            thread_repo, tweet_repo, fetcher = setup_tweet_deps()
            # or with error simulation:
            thread_repo, tweet_repo, fetcher = setup_tweet_deps(fetcher_should_fail=True)
            # Cleanup is automatic!
    """

    def _setup(
        fetcher_should_fail: bool = False,
    ) -> tuple[StubTweetThreadRepository, StubTweetRepository, StubTwitterFetcher]:
        thread_repo = StubTweetThreadRepository()
        tweet_repo = StubTweetRepository()
        fetcher = StubTwitterFetcher(should_fail=fetcher_should_fail)

        app.dependency_overrides[get_tweet_thread_repository] = lambda: thread_repo
        app.dependency_overrides[get_tweet_repository] = lambda: tweet_repo
        app.dependency_overrides[get_twitter_fetcher] = lambda: fetcher
        app.dependency_overrides[get_llm_client] = lambda: StubLLMClient()
        app.dependency_overrides[get_embedding_client] = lambda: StubEmbeddingClient()

        return thread_repo, tweet_repo, fetcher

    yield _setup
    app.dependency_overrides.clear()
