from sqlmodel import Field, SQLModel, Relationship, UniqueConstraint, Column, JSON
from datetime import datetime, timezone
from pgvector.sqlalchemy import Vector
from typing import Optional, TYPE_CHECKING, cast, Literal
from src.types import Embedding
from src.config import settings

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import ColumnElement

metadata = SQLModel.metadata


class BookBase(SQLModel):
    """Base model with shared fields"""

    title: str
    author: str


# Type alias - BookCreate is identical to BookBase
BookCreate = BookBase


class Book(BookBase, table=True):
    """Database table model"""

    __table_args__ = (UniqueConstraint("title", "author", name="uix_title_author"),)

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Relationship
    notes: list["Note"] = Relationship(back_populates="book")


class BookResponse(SQLModel):
    """Model for API responses (id is guaranteed to exist)"""

    id: int
    title: str
    author: str
    created_at: datetime


class BookWithNotesResponse(BookResponse):
    note_count: int


class NoteBase(SQLModel):
    """Base model with shared fields"""

    content: str
    content_hash: str = Field(unique=True)
    book_id: int = Field(foreign_key="book.id")
    embedding: Optional[Embedding] = None


# Type alias - NoteCreate is identical to NoteBase
NoteCreate = NoteBase


class Note(NoteBase, table=True):
    """Database table model"""

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    embedding: Optional[Embedding] = Field(
        default=None, sa_column=Column("embedding", Vector(1536))
    )  # OpenAI embeddings are 1536 dimensions

    # Relationships
    book: Book = Relationship(back_populates="notes")
    evaluations: list["Evaluation"] = Relationship(back_populates="note")

    @classmethod
    def embedding_cosine_distance(cls, target: Embedding) -> "ColumnElement[float]":
        """Calculate cosine distance to target embedding."""
        embedding_col = cast("ColumnElement[Vector]", cls.__table__.c.embedding)  # type: ignore
        return embedding_col.cosine_distance(target)

    @classmethod
    def embedding_is_not_null(cls) -> "ColumnElement[bool]":
        """Check if embedding is not null."""
        embedding_col = cast("ColumnElement[Vector]", cls.__table__.c.embedding)  # type: ignore
        return embedding_col.is_not(None)


class NoteRead(NoteBase):
    """Model for repository operations (id is guaranteed to exist)"""

    id: int
    created_at: datetime


class NoteResponse(SQLModel):
    """Model for API responses (id is guaranteed to exist)"""

    id: int
    content: str
    created_at: datetime


class NoteWithRelatedNotesResponse(SQLModel):
    """Model for note with related notes (used in streaming endpoints before AI context is generated)"""

    book: BookResponse
    note: NoteResponse
    related_notes: list[NoteResponse]


class BookWithNoteResponses(SQLModel):
    book: BookResponse
    notes: list[NoteResponse]


class Evaluation(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    score: float = Field(ge=0.0, le=1.0)
    prompt: str
    response: str
    analysis: str
    model_name: str = Field(default_factory=lambda: settings.default_evaluation_model)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Foreign key to Note
    note_id: int = Field(foreign_key="note.id")
    # Relationship
    note: Note = Relationship(back_populates="evaluations")


class NoteEvaluationHistory(SQLModel):
    """Historical evaluations for a single note"""

    note: NoteResponse
    evaluations: list[Evaluation]


# URL Models


class URLBase(SQLModel):
    """Base model with shared fields"""

    url: str
    title: str


# Type alias - URLCreate is identical to URLBase
URLCreate = URLBase


class URL(URLBase, table=True):
    """Database table model"""

    __table_args__ = (UniqueConstraint("url", name="uix_url"),)

    id: int | None = Field(default=None, primary_key=True)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Relationship
    chunks: list["URLChunk"] = Relationship(back_populates="url")


class URLResponse(SQLModel):
    """Model for API responses (id is guaranteed to exist)"""

    id: int
    url: str
    title: str
    fetched_at: datetime
    created_at: datetime


class URLWithChunksResponse(URLResponse):
    chunk_count: int


# URLChunk Models


class URLChunkBase(SQLModel):
    """Base model with shared fields"""

    content: str
    content_hash: str = Field(unique=True)
    url_id: int = Field(foreign_key="url.id")
    chunk_order: int
    is_summary: bool = False
    embedding: Optional[Embedding] = None


# Type alias - URLChunkCreate is identical to URLChunkBase
URLChunkCreate = URLChunkBase


class URLChunk(URLChunkBase, table=True):
    """Database table model"""

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    embedding: Optional[Embedding] = Field(
        default=None,
        sa_column=Column("embedding", Vector(settings.embedding_dimension)),
    )

    # Relationships
    url: URL = Relationship(back_populates="chunks")

    @classmethod
    def embedding_cosine_distance(cls, target: Embedding) -> "ColumnElement[float]":
        """Calculate cosine distance to target embedding."""
        embedding_col = cast("ColumnElement[Vector]", cls.__table__.c.embedding)  # type: ignore
        return embedding_col.cosine_distance(target)

    @classmethod
    def embedding_is_not_null(cls) -> "ColumnElement[bool]":
        """Check if embedding is not null."""
        embedding_col = cast("ColumnElement[Vector]", cls.__table__.c.embedding)  # type: ignore
        return embedding_col.is_not(None)


class URLChunkRead(URLChunkBase):
    """Model for repository operations (id is guaranteed to exist)"""

    id: int
    created_at: datetime


class URLChunkResponse(SQLModel):
    """Model for API responses (id is guaranteed to exist)"""

    id: int
    content: str
    chunk_order: int
    is_summary: bool
    created_at: datetime


class URLWithChunksResponses(SQLModel):
    url: URLResponse
    chunks: list[URLChunkResponse]


class SearchResult(SQLModel):
    query: str
    results: list[
        BookWithNoteResponses
    ]  # Deprecated: use 'books' field instead (kept for backwards compatibility)
    books: list[BookWithNoteResponses]
    urls: list[URLWithChunksResponses]
    tweet_threads: list["TweetThreadWithTweetsResponse"] = Field(default_factory=list)
    count: int


# TweetThread Models


class TweetThreadBase(SQLModel):
    """Base model with shared fields"""

    root_tweet_id: str
    author_username: str
    author_display_name: str
    title: str


# Type alias - TweetThreadCreate is identical to TweetThreadBase
TweetThreadCreate = TweetThreadBase


class TweetThread(TweetThreadBase, table=True):
    """Database table model"""

    __table_args__ = (UniqueConstraint("root_tweet_id", name="uix_root_tweet_id"),)

    id: int | None = Field(default=None, primary_key=True)
    tweet_count: int = Field(default=0)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Relationship
    tweets: list["Tweet"] = Relationship(back_populates="thread")


class TweetThreadResponse(SQLModel):
    """Model for API responses (id is guaranteed to exist)"""

    id: int
    root_tweet_id: str
    author_username: str
    author_display_name: str
    title: str
    tweet_count: int
    fetched_at: datetime
    created_at: datetime


# Tweet Models


class TweetBase(SQLModel):
    """Base model with shared fields"""

    tweet_id: str
    author_username: str
    author_display_name: str
    content: str
    media_urls: list[str] = Field(default_factory=list)
    thread_id: int = Field(foreign_key="tweetthread.id")
    position_in_thread: int
    tweeted_at: datetime
    embedding: Optional[Embedding] = None


# Type alias - TweetCreate is identical to TweetBase
TweetCreate = TweetBase


class Tweet(TweetBase, table=True):
    """Database table model"""

    __table_args__ = (UniqueConstraint("tweet_id", name="uix_tweet_id"),)

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    media_urls: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    embedding: Optional[Embedding] = Field(
        default=None,
        sa_column=Column("embedding", Vector(settings.embedding_dimension)),
    )

    # Relationships
    thread: TweetThread = Relationship(back_populates="tweets")

    @classmethod
    def embedding_cosine_distance(cls, target: Embedding) -> "ColumnElement[float]":
        """Calculate cosine distance to target embedding."""
        embedding_col = cast("ColumnElement[Vector]", cls.__table__.c.embedding)  # type: ignore
        return embedding_col.cosine_distance(target)

    @classmethod
    def embedding_is_not_null(cls) -> "ColumnElement[bool]":
        """Check if embedding is not null."""
        embedding_col = cast("ColumnElement[Vector]", cls.__table__.c.embedding)  # type: ignore
        return embedding_col.is_not(None)


class TweetRead(TweetBase):
    """Model for repository operations (id is guaranteed to exist)"""

    id: int
    created_at: datetime


class TweetResponse(SQLModel):
    """Model for API responses (id is guaranteed to exist)"""

    id: int
    tweet_id: str
    author_username: str
    author_display_name: str
    content: str
    media_urls: list[str]
    position_in_thread: int
    tweeted_at: datetime
    created_at: datetime


class TweetThreadWithTweetsResponse(SQLModel):
    """Model for thread with all tweets"""

    thread: TweetThreadResponse
    tweets: list[TweetResponse]


# Unified Response Models (for /random endpoint)
# Uses discriminated unions for type safety


class BookSource(SQLModel):
    """Book source - for notes"""

    id: int
    title: str
    type: Literal["book"]
    author: str
    created_at: datetime


class URLSource(SQLModel):
    """URL source - for URL chunks"""

    id: int
    title: str
    type: Literal["url"]
    url: str
    created_at: datetime


class TweetThreadSource(SQLModel):
    """Tweet thread source - for tweets"""

    id: int
    title: str
    type: Literal["tweet_thread"]
    author_username: str
    author_display_name: str
    root_tweet_id: str
    tweet_count: int
    created_at: datetime


# Type alias for source union
SourceResponse = BookSource | URLSource | TweetThreadSource


class NoteContent(SQLModel):
    """Note content item"""

    id: int
    content_type: Literal["note"]
    content: str
    created_at: datetime


class URLChunkContent(SQLModel):
    """URL chunk content item"""

    id: int
    content_type: Literal["url_chunk"]
    content: str
    is_summary: bool
    chunk_order: int
    created_at: datetime


class TweetContent(SQLModel):
    """Tweet content item"""

    id: int
    content_type: Literal["tweet"]
    content: str
    author_username: str
    position_in_thread: int
    media_urls: list[str]
    tweeted_at: datetime
    created_at: datetime


# Type alias for content union
ContentItemResponse = NoteContent | URLChunkContent | TweetContent


class ContentWithRelatedItemsResponse(SQLModel):
    """Unified response for random/streaming endpoints"""

    source: BookSource | URLSource | TweetThreadSource
    content: NoteContent | URLChunkContent | TweetContent
    related_items: list[NoteContent | URLChunkContent | TweetContent]
