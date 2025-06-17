"""Web utilities for fetching and processing web content."""

import asyncio
import io
import logging
import mimetypes
from typing import TypedDict

import aiohttp
import bs4

logger = logging.getLogger(__name__)


class Preview(TypedDict):
    title: str | None
    description: str | None
    image: str | io.BytesIO | None
    site_name: str | None
    url: str | None


async def get_website_preview(url: str) -> Preview:
    """
    Fetches a webpage and tries to extract metadata in a manner similar to social platforms (e.g., Twitter or Discord previews).

    Returns:
        A dict with the following keys
        - title
        - description
        - image
        - site_name
        - url
    """
    preview: Preview = {
        "title": None,
        "description": None,
        "image": None,
        "site_name": None,
        "url": None,
    }

    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )

    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.get(url, headers={"User-Agent": user_agent}) as response:
                response.raise_for_status()
                content_type = response.headers.get("Content-Type")
                if not content_type:
                    content_type, _ = mimetypes.guess_type(url, strict=False)

                # If we can't find a content type, assume it's a webpage
                if not content_type:
                    logger.warning(f"Could not determine content type for URL '{url}'")
                    content_type = "text/html"

                # If it's a video, extract first frame
                if content_type.startswith("video/"):
                    preview["image"] = await extract_first_frame(url)
                    preview["url"] = url
                    return preview

                page_text = await response.text()
    except aiohttp.ClientError as e:
        logger.debug(f"Failed to retrieve URL '{url}': {e}")
        return preview

    soup = bs4.BeautifulSoup(page_text, "html.parser")

    def get_meta_content(property_name: str, attribute_type: str = "property") -> str | None:
        """Helper function to extract content from meta tags."""
        tag = soup.find("meta", attrs={attribute_type: property_name})

        assert isinstance(tag, bs4.element.Tag | None), "tag is not a BeautifulSoup Tag or None"
        if tag and tag.get("content"):
            content = tag["content"]
            assert isinstance(content, str), "tag['content'] is not a string"
            return content.strip()
        return None

    # Check Open Graph first (e.g. <meta property="og:title" content="..." />)
    preview["title"] = get_meta_content("og:title") or get_meta_content("twitter:title", "name")
    preview["description"] = get_meta_content("og:description") or get_meta_content("twitter:description", "name")
    preview["image"] = get_meta_content("og:image") or get_meta_content("twitter:image", "name")
    preview["site_name"] = get_meta_content("og:site_name")
    preview["url"] = get_meta_content("og:url")

    # Fallbacks if OG/Twitter meta not found:
    # title: <title> tag
    if not preview["title"]:
        if soup.title and soup.title.string:
            preview["title"] = soup.title.string.strip()
    # description: <meta name="description" content="..." />
    if not preview["description"]:
        preview["description"] = get_meta_content("description", "name")
    # site_name: domain from given url
    if not preview["site_name"]:
        preview["site_name"] = url.split("//")[-1].split("/")[0]
    # url: use the original
    if not preview["url"]:
        preview["url"] = url

    return preview


async def extract_first_frame(video_url: str) -> io.BytesIO:
    """
    Asynchronously extract the first frame from a remote video URL

    Args:
        video_url: the URL of the video to extract the frame from

    Returns:
        A BytesIO object containing the extracted frame

    Raises:
        RuntimeError: if the ffmpeg process fails
    """
    # fmt: off
    cmd = [
        "ffmpeg",
        "-i", video_url,
        "-frames:v", "1",
        "-f", "image2pipe",
        "-vcodec", "png",
        "pipe:1"
    ]
    # fmt: on

    # Run ffmpeg asynchronously
    # Note that you cannot use WindowsSelectorEventLoopPolicy with asyncio.run() on Windows
    process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await process.communicate()

    if process.returncode != 0:
        raise RuntimeError(f"ffmpeg process failed. stderr: {err.decode('utf-8', errors='ignore')}")

    return io.BytesIO(out)
