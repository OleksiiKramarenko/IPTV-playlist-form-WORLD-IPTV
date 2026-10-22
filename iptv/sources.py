"""Downloading source playlists."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, Sequence

import aiohttp

from . import m3u
from .checker import USER_AGENT
from .config import Source

log = logging.getLogger(__name__)

SOURCE_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class SourceResult:
    source: Source
    entries: tuple[m3u.Entry, ...]
    error: Optional[str] = None


async def fetch_sources(sources: Sequence[Source]) -> list[SourceResult]:
    timeout = aiohttp.ClientTimeout(total=SOURCE_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": USER_AGENT}) as session:
        return list(await asyncio.gather(*(_fetch_one(session, s) for s in sources)))


async def _fetch_one(session: aiohttp.ClientSession, source: Source) -> SourceResult:
    try:
        async with session.get(source.url) as resp:
            resp.raise_for_status()
            text = await resp.text(encoding="utf-8", errors="replace")
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.warning("Source %s failed: %s", source.name, e)
        return SourceResult(source=source, entries=(), error=str(e) or type(e).__name__)

    entries = tuple(m3u.parse(text))
    if not entries:
        log.warning("Source %s returned no entries", source.name)
        return SourceResult(source=source, entries=(), error="no entries")
    log.info("Source %s: %d entries", source.name, len(entries))
    return SourceResult(source=source, entries=entries)
