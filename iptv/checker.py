"""Stream liveness check that follows HLS down to a real media segment.

Besides "plays or not", each check reports a fingerprint of the media the stream ends in
(see stubs.py) and flags short looped VOD playlists - the usual "offline" / "pay up" screen.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import urljoin

import aiohttp

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
MANIFEST_READ_BYTES = 256 * 1024
SEGMENT_READ_BYTES = 16 * 1024
MAX_PLAYLIST_DEPTH = 3
PROGRESS_EVERY = 200
# A live channel never ends; a finished playlist shorter than this is a looped stub screen.
LOOP_MAX_SECONDS = 300
_EXTINF_RE = re.compile(r"#EXTINF:\s*([\d.]+)")


@dataclass(frozen=True)
class CheckResult:
    url: str
    ok: bool
    latency_ms: Optional[float] = None
    fingerprint: Optional[str] = None
    stub: bool = False


class _NotPlayable(Exception):
    pass


class _StubScreen(_NotPlayable):
    pass


async def check_urls(urls: Iterable[str], timeout: float, workers: int) -> dict[str, CheckResult]:
    unique = list(dict.fromkeys(urls))
    semaphore = asyncio.Semaphore(workers)
    done = 0

    async def check_one(session: aiohttp.ClientSession, url: str) -> CheckResult:
        nonlocal done
        async with semaphore:
            result = await check_stream(session, url, timeout)
        done += 1
        if done % PROGRESS_EVERY == 0:
            log.info("  checked %d/%d", done, len(unique))
        return result

    # Many IPTV hosts serve broken TLS chains; nothing sensitive is sent, so verification is off.
    connector = aiohttp.TCPConnector(limit=workers, ssl=False)
    async with aiohttp.ClientSession(connector=connector, headers={"User-Agent": USER_AGENT}) as session:
        results = await asyncio.gather(*(check_one(session, u) for u in unique))
    return {r.url: r for r in results}


async def check_stream(session: aiohttp.ClientSession, url: str, timeout: float) -> CheckResult:
    started = time.monotonic()
    try:
        fingerprint = await asyncio.wait_for(_follow(session, url, depth=0), timeout=timeout)
    except _StubScreen:
        return CheckResult(url=url, ok=False, stub=True)
    except (_NotPlayable, aiohttp.ClientError, asyncio.TimeoutError, UnicodeError, ValueError):
        return CheckResult(url=url, ok=False)
    latency_ms = round((time.monotonic() - started) * 1000, 1)
    return CheckResult(url=url, ok=True, latency_ms=latency_ms, fingerprint=fingerprint)


async def _follow(session: aiohttp.ClientSession, url: str, depth: int) -> str:
    """Walk playlists down to media bytes and return their fingerprint."""
    playlist, final_url, head = await _fetch(session, url)
    if playlist is None:
        # Media segment, MPEG-TS stream or DASH manifest: having bytes is enough.
        return hashlib.sha1(head).hexdigest()
    if depth >= MAX_PLAYLIST_DEPTH:
        raise _NotPlayable("playlist nesting too deep")
    if is_short_loop(playlist):
        raise _StubScreen("short finished playlist")
    next_uri = next_uri_of(playlist)
    if next_uri is None:
        raise _NotPlayable("empty playlist")
    return await _follow(session, urljoin(final_url, next_uri), depth + 1)


async def _fetch(session: aiohttp.ClientSession, url: str) -> tuple[Optional[str], str, bytes]:
    """Return (playlist text or None for media, final URL after redirects, first bytes of the body)."""
    async with session.get(url, allow_redirects=True) as resp:
        if resp.status >= 400:
            raise _NotPlayable(f"HTTP {resp.status}")
        if "text/html" in resp.headers.get("Content-Type", "").lower():
            raise _NotPlayable("HTML page instead of a stream")
        head = await _read_up_to(resp.content, SEGMENT_READ_BYTES)
        if not head:
            raise _NotPlayable("empty body")
        if not head.lstrip(b"\xef\xbb\xbf \r\n").startswith(b"#EXTM3U"):
            return None, str(resp.url), head
        rest = await resp.content.read(MANIFEST_READ_BYTES - len(head))
        return (head + rest).decode("utf-8", "ignore"), str(resp.url), head


async def _read_up_to(stream: aiohttp.StreamReader, size: int) -> bytes:
    """read(n) returns whatever chunk arrived first; fingerprints need the same prefix every time."""
    data = b""
    while len(data) < size:
        chunk = await stream.read(size - len(data))
        if not chunk:
            break
        data += chunk
    return data


def is_short_loop(playlist: str) -> bool:
    if "#EXT-X-ENDLIST" not in playlist:
        return False
    return sum(float(d) for d in _EXTINF_RE.findall(playlist)) < LOOP_MAX_SECONDS


def next_uri_of(playlist: str) -> Optional[str]:
    """First variant of a master playlist, or the newest segment of a media playlist."""
    uris = [line.strip() for line in playlist.splitlines() if line.strip() and not line.startswith("#")]
    if not uris:
        return None
    return uris[0] if "#EXT-X-STREAM-INF" in playlist else uris[-1]
