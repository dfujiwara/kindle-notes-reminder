"""
URL fetching and content extraction utilities.

Fetches URLs and extracts clean, readable text from HTML content using httpx and BeautifulSoup.
"""

import httpx
import ipaddress
import logging
import socket
from bs4 import BeautifulSoup
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse
import re

from src.config import settings

logger = logging.getLogger(__name__)


class URLFetchError(Exception):
    """Exception raised when fetching or parsing URL content fails"""

    pass


@dataclass
class FetchedContent:
    """Result of fetching and parsing a URL"""

    url: str
    title: str
    content: str


class URLFetcherInterface(Protocol):
    """Protocol for URL fetching implementations."""

    async def __call__(
        self, url: str, max_content_size: int | None = None
    ) -> FetchedContent:
        """
        Fetch a URL and extract clean text content.

        Args:
            url: The URL to fetch
            max_content_size: Maximum allowed content size in bytes (optional)

        Returns:
            FetchedContent: The fetched URL, title, and clean text content

        Raises:
            URLFetchError: If fetching or parsing fails
        """
        ...


BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def validate_url_target(url: str) -> None:
    """Validate that a URL does not target internal or private network addresses.

    Resolves the hostname to IP addresses and checks against blocked private/reserved
    network ranges to prevent SSRF attacks.

    Raises:
        URLFetchError: If the URL targets a blocked network or cannot be resolved.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise URLFetchError(f"Invalid URL (no hostname): {url}")

    try:
        addrinfo = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise URLFetchError(f"Cannot resolve hostname: {hostname}")

    for _family, _type, _proto, _canonname, sockaddr in addrinfo:
        ip = ipaddress.ip_address(sockaddr[0])
        for network in BLOCKED_NETWORKS:
            if ip in network:
                raise URLFetchError(f"URL targets a blocked network ({network}): {url}")


supported_content_type = {"text/html", "application/xhtml", "text/plain"}
# Unwanted tags (including head which contains title and meta)
html_tags_to_remove = ["script", "style", "nav", "footer", "header", "head"]


async def fetch_url_content(
    url: str, max_content_size: int | None = None
) -> FetchedContent:
    """
    Fetch a URL and extract clean text content from the HTML.

    Args:
        url: The URL to fetch
        max_content_size: Maximum allowed content size in bytes (default from settings)

    Returns:
        FetchedContent: The fetched URL, extracted title, and clean text content

    Raises:
        URLFetchError: If fetching or parsing fails
    """
    max_content_size = max_content_size or settings.max_url_content_size
    timeout = httpx.Timeout(settings.url_fetch_timeout)

    validate_url_target(url)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            logger.info(f"Fetching URL: {url}")
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()

            # Check content-type is HTML-like
            content_type = response.headers.get("content-type", "").lower()
            if content_type not in supported_content_type:
                logger.warning(f"Unexpected content-type for {url}: {content_type}")

            html_content = response.text
            if len(html_content.encode("utf-8")) > max_content_size:
                raise URLFetchError(
                    f"Content too large ({len(html_content)} chars, "
                    f"max {max_content_size}): {url}"
                )
            logger.info(
                f"Successfully fetched {len(html_content)} characters from {url}"
            )
            return _parse_html_content(html_content, url)

    except httpx.TimeoutException as e:
        raise URLFetchError(f"Timeout fetching URL: {url}") from e
    except httpx.HTTPStatusError as e:
        raise URLFetchError(f"HTTP error {e.response.status_code}: {url}") from e
    except httpx.RequestError as e:
        raise URLFetchError(f"Request failed for {url}: {str(e)}") from e
    except URLFetchError:
        raise  # Re-raise URLFetchError as-is
    except Exception as e:
        logger.error(f"Unexpected error fetching {url}: {str(e)}")
        raise URLFetchError(f"Error fetching URL {url}: {str(e)}") from e


def _parse_html_content(html_content: str, url: str) -> FetchedContent:
    """
    Parse HTML content and extract title and clean text.

    Args:
        html_content: Raw HTML content
        url: The URL (used as fallback for title)

    Returns:
        FetchedContent with extracted title and clean text

    Raises:
        URLFetchError: If parsing fails or content is empty
    """
    try:
        soup = BeautifulSoup(html_content, "html.parser")

        title = url
        title_tag = soup.find("title")
        if title_tag:
            title_text = title_tag.get_text(strip=True)
            title = title_text or title

        for tag in soup.find_all(html_tags_to_remove):
            tag.decompose()

        # Extract text from body if it exists, otherwise from remaining content
        body = soup.find("body")
        if body:
            text = body.get_text(separator="\n\n", strip=True)
        else:
            text = soup.get_text(separator="\n\n", strip=True)

        # Clean excessive whitespace
        # Replace 3+ consecutive newlines with double newline
        text = re.sub(r"\n\n\n+", "\n\n", text)
        text = text.strip()

        # Verify we have content
        if not text:
            raise URLFetchError(f"No text content extracted from {url}")
        logger.info(f"Extracted {len(text)} characters of content from {url}")

        return FetchedContent(url=url, title=title, content=text)
    except URLFetchError:
        raise  # Re-raise URLFetchError as-is
    except Exception as e:
        logger.error(f"Error parsing HTML from {url}: {str(e)}")
        raise URLFetchError(f"Error parsing content from {url}: {str(e)}") from e
