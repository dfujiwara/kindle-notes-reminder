"""
Tests for URL fetching and content extraction functionality.
"""

from unittest.mock import patch

import httpx
import pytest
import respx
from httpx import Response

from src.url_ingestion.url_fetcher import (
    fetch_url_content,
    validate_url_target,
    URLFetchError,
)

# Patch out SSRF validation for HTTP-level tests (DNS unavailable in test env)
pytestmark = pytest.mark.usefixtures("bypass_ssrf_check")


@pytest.fixture(autouse=True)
def bypass_ssrf_check():
    with patch("src.url_ingestion.url_fetcher.validate_url_target"):
        yield


@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_success():
    """Test successful URL fetch with valid HTML content."""
    html_content = """
    <html>
        <head><title>Test Article</title></head>
        <body>
            <p>First paragraph with content.</p>
            <p>Second paragraph with more content.</p>
        </body>
    </html>
    """

    respx.get("https://example.com/article").mock(
        return_value=Response(
            200, content=html_content, headers={"content-type": "text/html"}
        )
    )

    result = await fetch_url_content("https://example.com/article")
    assert result.url == "https://example.com/article"
    assert result.title == "Test Article"
    assert "First paragraph" in result.content
    assert "Second paragraph" in result.content


@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_fallback_title_to_url():
    """Test that URL is used as title when <title> tag is missing."""
    html_content = """
    <html>
        <head></head>
        <body><p>Content without title tag.</p></body>
    </html>
    """
    respx.get("https://example.com/no-title").mock(
        return_value=Response(200, content=html_content)
    )
    result = await fetch_url_content("https://example.com/no-title")
    assert result.title == "https://example.com/no-title"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_removes_script_tags():
    """Test that <script> tags and their content are removed."""
    html_content = """
    <html>
        <body>
            <p>Visible content.</p>
            <script>alert('This should not appear');</script>
            <p>More visible content.</p>
        </body>
    </html>
    """

    respx.get("https://example.com/with-script").mock(
        return_value=Response(200, content=html_content)
    )

    result = await fetch_url_content("https://example.com/with-script")
    assert "alert" not in result.content
    assert "This should not appear" not in result.content
    assert "Visible content" in result.content


@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_removes_style_tags():
    """Test that <style> tags are removed."""
    html_content = """
    <html>
        <head>
            <style>body { color: red; }</style>
        </head>
        <body><p>Content here.</p></body>
    </html>
    """

    respx.get("https://example.com/with-style").mock(
        return_value=Response(200, content=html_content)
    )
    result = await fetch_url_content("https://example.com/with-style")
    assert "color: red" not in result.content
    assert "Content here" in result.content


@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_removes_nav_tag():
    """Test that <nav> tags are removed."""
    html_content = """
    <html>
        <body>
            <nav>Navigation menu ignored</nav>
            <p>Main content.</p>
        </body>
    </html>
    """

    respx.get("https://example.com/with-nav").mock(
        return_value=Response(200, content=html_content)
    )
    result = await fetch_url_content("https://example.com/with-nav")
    assert "Navigation menu" not in result.content
    assert "Main content" in result.content


@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_removes_footer_tag():
    """Test that <footer> tags are removed."""
    html_content = """
    <html>
        <body>
            <p>Main content.</p>
            <footer>Copyright 2024</footer>
        </body>
    </html>
    """

    respx.get("https://example.com/with-footer").mock(
        return_value=Response(200, content=html_content)
    )
    result = await fetch_url_content("https://example.com/with-footer")
    assert "Copyright" not in result.content
    assert "Main content" in result.content


@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_removes_header_tag():
    """Test that <header> tags are removed."""
    html_content = """
    <html>
        <body>
            <header>Site Header</header>
            <p>Main content.</p>
        </body>
    </html>
    """

    respx.get("https://example.com/with-header").mock(
        return_value=Response(200, content=html_content)
    )
    result = await fetch_url_content("https://example.com/with-header")
    assert "Site Header" not in result.content
    assert "Main content" in result.content


@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_preserves_paragraph_structure():
    """Test that paragraphs are separated by double newlines."""
    html_content = """
    <html>
        <body>
            <p>First paragraph.</p>
            <p>Second paragraph.</p>
            <p>Third paragraph.</p>
        </body>
    </html>
    """

    respx.get("https://example.com/paragraphs").mock(
        return_value=Response(200, content=html_content)
    )

    result = await fetch_url_content("https://example.com/paragraphs")

    # Should have double newlines between paragraphs
    assert "\n\n" in result.content
    assert "First paragraph" in result.content
    assert "Second paragraph" in result.content
    assert "Third paragraph" in result.content


@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_cleans_excessive_whitespace():
    """Test that excessive newlines are cleaned up."""
    html_content = """
    <html>
        <body>
            <p>First paragraph.</p>


            <p>Second paragraph with extra spacing.</p>
        </body>
    </html>
    """

    respx.get("https://example.com/whitespace").mock(
        return_value=Response(200, content=html_content)
    )
    result = await fetch_url_content("https://example.com/whitespace")

    # Should not have 3+ consecutive newlines
    assert "\n\n\n" not in result.content


@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_timeout():
    """Test that timeout exception is converted to URLFetchError."""
    respx.get("https://example.com/timeout").mock(
        side_effect=httpx.TimeoutException("Connection timeout")
    )
    with pytest.raises(URLFetchError, match="Timeout"):
        await fetch_url_content("https://example.com/timeout")


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 403, 404, 500, 502, 503])
@respx.mock
async def test_fetch_url_http_error(status_code: int):
    """Test that HTTP errors raise URLFetchError."""
    respx.get("https://example.com/error").mock(return_value=Response(status_code))

    with pytest.raises(URLFetchError, match=f"HTTP error {status_code}"):
        await fetch_url_content("https://example.com/error")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_content_too_large():
    """Test that oversized content is rejected."""
    large_content = "x" * 20
    respx.get("https://example.com/large").mock(
        return_value=Response(200, content=large_content)
    )
    with pytest.raises(URLFetchError, match="Content too large"):
        await fetch_url_content("https://example.com/large", max_content_size=10)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_empty_content():
    """Test that pages with no text content raise URLFetchError."""
    html_content = """
    <html>
        <head><title>Empty Page</title></head>
        <body></body>
    </html>
    """
    respx.get("https://example.com/empty").mock(
        return_value=Response(200, content=html_content)
    )
    with pytest.raises(URLFetchError, match="No text content"):
        await fetch_url_content("https://example.com/empty")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_only_whitespace():
    """Test that pages with only whitespace are treated as empty."""
    html_content = """
    <html>
        <head><title>Whitespace Page</title></head>
        <body>
        </body>
    </html>
    """
    respx.get("https://example.com/whitespace-only").mock(
        return_value=Response(200, content=html_content)
    )
    with pytest.raises(URLFetchError, match="No text content"):
        await fetch_url_content("https://example.com/whitespace-only")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_complex_html():
    """Test with more complex, realistic HTML."""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Comprehensive Article Title</title>
        <style>body { font-family: Arial; }</style>
    </head>
    <body>
        <header>
            <h1>Site Header</h1>
            <nav>Navigation items</nav>
        </header>

        <article>
            <h2>Article Heading</h2>
            <p>Introduction paragraph with some content.</p>

            <p>Body paragraph with more detailed content and information.</p>

            <p>Conclusion paragraph summarizing the article.</p>
        </article>

        <footer>
            <p>Copyright 2024 Example Site</p>
        </footer>

        <script>
            console.log('Analytics script');
        </script>
    </body>
    </html>
    """

    respx.get("https://example.com/article").mock(
        return_value=Response(
            200, content=html_content, headers={"content-type": "text/html"}
        )
    )

    result = await fetch_url_content("https://example.com/article")

    # Should have title
    assert result.title == "Comprehensive Article Title"

    # Should have main content
    assert "Introduction paragraph" in result.content
    assert "Body paragraph" in result.content
    assert "Conclusion paragraph" in result.content

    # Should NOT have removed content
    assert "Site Header" not in result.content
    assert "Navigation items" not in result.content
    assert "Copyright" not in result.content
    assert "console.log" not in result.content
    assert "font-family" not in result.content


# --- SSRF validation tests (these don't use bypass_ssrf_check) ---


class TestValidateUrlTarget:
    """Tests for SSRF protection via validate_url_target."""

    def _mock_resolve(self, ip: str):
        """Return a mock getaddrinfo result for a single IP."""
        import socket

        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 0, "", (ip, 0))]

    def test_blocks_localhost(self):
        with patch("socket.getaddrinfo", return_value=self._mock_resolve("127.0.0.1")):
            with pytest.raises(URLFetchError, match="blocked network"):
                validate_url_target("https://localhost/secret")

    def test_blocks_private_10(self):
        with patch("socket.getaddrinfo", return_value=self._mock_resolve("10.0.0.1")):
            with pytest.raises(URLFetchError, match="blocked network"):
                validate_url_target("https://internal.corp/data")

    def test_blocks_private_172(self):
        with patch("socket.getaddrinfo", return_value=self._mock_resolve("172.16.0.1")):
            with pytest.raises(URLFetchError, match="blocked network"):
                validate_url_target("https://internal.corp/data")

    def test_blocks_private_192(self):
        with patch(
            "socket.getaddrinfo", return_value=self._mock_resolve("192.168.1.1")
        ):
            with pytest.raises(URLFetchError, match="blocked network"):
                validate_url_target("https://home.local/admin")

    def test_blocks_metadata_endpoint(self):
        with patch(
            "socket.getaddrinfo", return_value=self._mock_resolve("169.254.169.254")
        ):
            with pytest.raises(URLFetchError, match="blocked network"):
                validate_url_target("http://169.254.169.254/latest/meta-data/")

    def test_blocks_ipv6_loopback(self):
        with patch("socket.getaddrinfo", return_value=self._mock_resolve("::1")):
            with pytest.raises(URLFetchError, match="blocked network"):
                validate_url_target("https://localhost/secret")

    def test_allows_public_ip(self):
        with patch(
            "socket.getaddrinfo", return_value=self._mock_resolve("93.184.216.34")
        ):
            validate_url_target("https://example.com/article")

    def test_rejects_no_hostname(self):
        with pytest.raises(URLFetchError, match="no hostname"):
            validate_url_target("not-a-url")

    def test_rejects_unresolvable_hostname(self):
        import socket as _socket

        with patch("socket.getaddrinfo", side_effect=_socket.gaierror):
            with pytest.raises(URLFetchError, match="Cannot resolve"):
                validate_url_target("https://nonexistent.invalid/page")
