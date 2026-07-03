"""
threads_poster.py

Sequential multi-part Threads posting via the Threads Graph API.

Threads API publishing is a TWO-STEP process per post:
  1. POST /{user_id}/threads          -> creates a media container, returns creation_id
  2. POST /{user_id}/threads_publish   -> publishes that container, returns the live post id (e.g. media_id)

To CHAIN posts into a single connected thread, each subsequent container is created
with `reply_to_id` set to the previously PUBLISHED post's id (not the creation_id).
There is no separate "add to thread" call -- the reply_to_id chain *is* the thread.

Rate limit note: Threads API recommends/requires a short delay between container
creation and publish for the container to finish processing server-side, especially
when an image/video is attached. A poll-based wait (check container status) is safer
than a fixed sleep for media posts; for text-only posts a short fixed delay is fine.

Usage:
    from threads_poster import ThreadsPoster

    poster = ThreadsPoster(
        access_token=os.environ["THREADS_ACCESS_TOKEN"],
        user_id=os.environ["THREADS_USER_ID"],
    )

    posts = [
        "1/ Man City just dropped their summer transfer shortlist...",
        "2/ Top of the list: a 22-year-old winger from Ligue 1...",
        "3/ Here's why the deal makes sense financially...",
    ]

    result = poster.post_thread(posts)
    print(result)  # list of dicts: [{"text": ..., "post_id": ...}, ...]
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger("threads_poster")

GRAPH_API_BASE = "https://graph.threads.net/v1.0"
DEFAULT_TIMEOUT = 30
CONTAINER_POLL_INTERVAL_SEC = 2
CONTAINER_POLL_MAX_ATTEMPTS = 10
INTER_POST_DELAY_SEC = 3  # small buffer between thread parts to avoid rate-limit blips


class ThreadsAPIError(Exception):
    """Raised when the Threads API returns an error response."""

    def __init__(self, message: str, status_code: Optional[int] = None, payload: Optional[dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


@dataclass
class ThreadPostResult:
    text: str
    post_id: str
    image_url: Optional[str] = None


class ThreadsPoster:
    def __init__(self, access_token: str, user_id: str, session: Optional[requests.Session] = None):
        if not access_token or not user_id:
            raise ValueError("access_token and user_id are required")
        self.access_token = access_token
        self.user_id = user_id
        self.session = session or requests.Session()

    # ------------------------------------------------------------------
    # Low-level API calls
    # ------------------------------------------------------------------

    def _create_container(
        self,
        text: str,
        reply_to_id: Optional[str] = None,
        image_url: Optional[str] = None,
    ) -> str:
        """Step 1: create a media container. Returns creation_id."""
        # Normalize whitespace: Threads strips \n\n but keeps single \n
        text = re.sub(r'\n{2,}', '\n', text)
        # Strip markdown italic/bold markers
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'\1', text)
        # Insert \n between sentences if not already separated
        text = re.sub(r'(?<!Mr)(?<!Mrs)(?<!Ms)(?<!Dr)(?<!St)(?<!vs)(?<!Jr)(?<!Sr)(?<!Prof)([.?!])\s+(?=[A-Z])', r'\1\n', text)
        url = f"{GRAPH_API_BASE}/{self.user_id}/threads"
        params = {
            "text": text,
            "access_token": self.access_token,
        }

        if image_url:
            params["media_type"] = "IMAGE"
            params["image_url"] = image_url
        else:
            params["media_type"] = "TEXT"

        if reply_to_id:
            params["reply_to_id"] = reply_to_id

        resp = self.session.post(url, data=params, timeout=DEFAULT_TIMEOUT)
        data = self._parse_response(resp)
        creation_id = data.get("id")
        if not creation_id:
            raise ThreadsAPIError(f"No creation_id returned: {data}", resp.status_code, data)

        logger.info("Created container %s (reply_to=%s)", creation_id, reply_to_id)
        return creation_id

    def _get_container_status(self, creation_id: str) -> str:
        """Check container processing status (mainly relevant for image/video)."""
        url = f"{GRAPH_API_BASE}/{creation_id}"
        params = {
            "fields": "status,error_message",
            "access_token": self.access_token,
        }
        resp = self.session.get(url, params=params, timeout=DEFAULT_TIMEOUT)
        data = self._parse_response(resp)
        return data.get("status", "UNKNOWN")

    def _wait_for_container_ready(self, creation_id: str, has_media: bool) -> None:
        """Poll until container is FINISHED. Text-only posts rarely need this,
        but it's cheap insurance and required for image posts."""
        if not has_media:
            time.sleep(1)  # tiny buffer is usually enough for text-only
            return

        for attempt in range(CONTAINER_POLL_MAX_ATTEMPTS):
            status = self._get_container_status(creation_id)
            if status == "FINISHED":
                return
            if status == "ERROR":
                raise ThreadsAPIError(f"Container {creation_id} failed processing")
            logger.info(
                "Container %s status=%s, waiting (%d/%d)",
                creation_id, status, attempt + 1, CONTAINER_POLL_MAX_ATTEMPTS,
            )
            time.sleep(CONTAINER_POLL_INTERVAL_SEC)

        raise ThreadsAPIError(f"Container {creation_id} did not finish processing in time")

    def _publish_container(self, creation_id: str) -> str:
        """Step 2: publish the container. Returns the live post id."""
        url = f"{GRAPH_API_BASE}/{self.user_id}/threads_publish"
        params = {
            "creation_id": creation_id,
            "access_token": self.access_token,
        }
        resp = self.session.post(url, data=params, timeout=DEFAULT_TIMEOUT)
        data = self._parse_response(resp)
        post_id = data.get("id")
        if not post_id:
            raise ThreadsAPIError(f"No post_id returned on publish: {data}", resp.status_code, data)

        logger.info("Published post %s", post_id)
        return post_id

    def get_permalink(self, post_id: str) -> str:
        """Fetch short permalink for a post via the API."""
        url = f"{GRAPH_API_BASE}/{post_id}"
        params = {"fields": "permalink", "access_token": self.access_token}
        try:
            resp = self.session.get(url, params=params, timeout=DEFAULT_TIMEOUT)
            data = resp.json()
            return data.get("permalink", "")
        except Exception:
            return ""

    @staticmethod
    def _parse_response(resp: requests.Response) -> dict:
        try:
            data = resp.json()
        except ValueError:
            raise ThreadsAPIError(f"Non-JSON response: {resp.text}", resp.status_code)

        if resp.status_code >= 400 or "error" in data:
            err = data.get("error", {})
            msg = err.get("message", str(data))
            raise ThreadsAPIError(msg, resp.status_code, data)

        return data

    # ------------------------------------------------------------------
    # Public single-post and thread-chaining methods
    # ------------------------------------------------------------------

    def post_single(
        self,
        text: str,
        reply_to_id: Optional[str] = None,
        image_url: Optional[str] = None,
    ) -> str:
        """Create + publish a single post (optionally as a reply to chain a thread).
        Returns the published post_id."""
        creation_id = self._create_container(text, reply_to_id=reply_to_id, image_url=image_url)
        self._wait_for_container_ready(creation_id, has_media=bool(image_url))
        return self._publish_container(creation_id)

    def post_thread(
        self,
        parts: list[str],
        image_urls: Optional[list[Optional[str]]] = None,
        stop_on_error: bool = True,
    ) -> list[ThreadPostResult]:
        """
        Post a full multi-part thread sequentially.

        parts: ordered list of post text, e.g. carousel slide captions from
               the Press Box Pipeline JSON output.
        image_urls: optional parallel list of image URLs (same length as parts,
                    use None for slides with no image).
        stop_on_error: if True, raises on first failure (partial thread may
                        already be live -- check results returned so far via
                        the exception's .args or catch and inspect `results`).

        Returns list of ThreadPostResult in posted order.
        """
        if not parts:
            raise ValueError("parts cannot be empty")

        if image_urls is not None and len(image_urls) != len(parts):
            raise ValueError("image_urls must be the same length as parts")

        results: list[ThreadPostResult] = []
        reply_to_id: Optional[str] = None

        for i, text in enumerate(parts):
            # Defensive: Threads API rejects >500 chars. Trim at 500 (no ellipsis — chars is chars).
            if len(text) > 500:
                logger.warning("Slide %d/%d is %d chars — trimming to 500", i + 1, len(parts), len(text))
                text = text[:500].rstrip()

            img = image_urls[i] if image_urls else None
            try:
                post_id = self.post_single(text, reply_to_id=reply_to_id, image_url=img)
            except ThreadsAPIError as e:
                logger.error("Failed posting part %d/%d: %s", i + 1, len(parts), e)
                if stop_on_error:
                    logger.error(
                        "Thread partially posted: %d/%d parts succeeded before failure.",
                        len(results), len(parts),
                    )
                    raise
                continue

            results.append(ThreadPostResult(text=text, post_id=post_id, image_url=img))
            reply_to_id = post_id  # next part replies to THIS published post

            if i < len(parts) - 1:
                time.sleep(INTER_POST_DELAY_SEC)

        logger.info("Thread complete: %d/%d parts posted.", len(results), len(parts))
        return results

    def get_metrics(self, post_id: str) -> Optional[dict]:
        """Pull engagement metrics for a post.
        
        Returns dict with views, likes, replies, shares or None on failure.
        Requires threads_manage_insights scope on the token.
        """
        url = f"{GRAPH_API_BASE}/{post_id}/insights"
        params = {
            "metric": "views,likes,replies,shares",
            "access_token": self.access_token,
        }
        try:
            resp = self.session.get(url, params=params, timeout=DEFAULT_TIMEOUT)
            data = self._parse_response(resp)
            metrics = {}
            for item in data.get("data", []):
                name = item.get("name")
                value = item.get("values", [{}])[0].get("value", 0)
                metrics[name] = value
            return metrics if metrics else None
        except Exception as e:
            logger.warning("Failed to get metrics for %s: %s", post_id, e)
            return None


# ----------------------------------------------------------------------
# Example: wiring into Press Box Pipeline carousel JSON output
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import json
    import os

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # Example carousel JSON shape coming out of the Press Box Pipeline.
    # Adjust the key names to match your actual pipeline output schema.
    example_carousel = {
        "slides": [
            {"caption": "1/ Big news from the Etihad this week...", "image_url": None},
            {"caption": "2/ Sources close to the club say...", "image_url": None},
            {"caption": "3/ Here's what it means for the squad...", "image_url": None},
        ]
    }

    poster = ThreadsPoster(
        access_token=os.environ["THREADS_ACCESS_TOKEN"],
        user_id=os.environ["THREADS_USER_ID"],
    )

    texts = [slide["caption"] for slide in example_carousel["slides"]]
    images = [slide.get("image_url") for slide in example_carousel["slides"]]

    thread_results = poster.post_thread(texts, image_urls=images)

    print(json.dumps([r.__dict__ for r in thread_results], indent=2))
