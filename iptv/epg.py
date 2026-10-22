"""Matching channels to XMLTV guide ids and filling missing logos from the guide.

Only the <channel> list at the top of each XMLTV file is read; the download stops at the
first <programme>, so even large guides cost a few hundred kilobytes.
"""

from __future__ import annotations

import asyncio
import logging
import re
import zlib
from dataclasses import dataclass, replace
from typing import Iterable, Mapping, Sequence
from xml.etree.ElementTree import ParseError, XMLPullParser

import aiohttp

from .channels import channel_key
from .checker import USER_AGENT
from .m3u import Entry

log = logging.getLogger(__name__)

EPG_TIMEOUT_SECONDS = 120
CHUNK_BYTES = 64 * 1024
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
GZIP_MAGIC = b"\x1f\x8b"
_COUNTRY_TAG_RE = re.compile(r"\s*\[[^\]]*\]\s*$")


@dataclass(frozen=True)
class GuideChannel:
    id: str
    names: tuple[str, ...]
    icon: str = ""


@dataclass(frozen=True)
class EpgIndex:
    icons: Mapping[str, str]      # guide id -> icon url ("" if none)
    by_name: Mapping[str, str]    # channel_key(display-name) -> guide id, unambiguous names only

    def __contains__(self, guide_id: str) -> bool:
        return guide_id in self.icons


class ChannelReader:
    """Incremental XMLTV reader: feed raw (optionally gzipped) bytes, collect <channel>s until <programme>."""

    def __init__(self) -> None:
        self._parser = XMLPullParser(events=("start", "end"))
        self._decompressor = None
        self._started = False
        self.done = False
        self.channels: list[GuideChannel] = []

    def feed(self, chunk: bytes) -> None:
        if self.done or not chunk:
            return
        if not self._started:
            self._started = True
            if chunk.startswith(GZIP_MAGIC):
                self._decompressor = zlib.decompressobj(wbits=31)
        self._parser.feed(self._decompressor.decompress(chunk) if self._decompressor else chunk)
        for event, el in self._parser.read_events():
            if event == "start" and el.tag == "programme":
                self.done = True
                return
            if event == "end" and el.tag == "channel":
                self.channels.append(_to_channel(el))
                el.clear()


def parse_channels(chunks: Iterable[bytes]) -> list[GuideChannel]:
    reader = ChannelReader()
    for chunk in chunks:
        reader.feed(chunk)
        if reader.done:
            break
    return reader.channels


def _to_channel(el) -> GuideChannel:
    icon = el.find("icon")
    names = tuple(n.text.strip() for n in el.findall("display-name") if n.text and n.text.strip())
    return GuideChannel(id=el.get("id", ""), names=names, icon=(icon.get("src", "") if icon is not None else ""))


def build_index(guides: Sequence[Sequence[GuideChannel]]) -> EpgIndex:
    """Merge guides in priority order. A name pointing at several ids within one guide is ambiguous and skipped."""
    icons: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for channels in guides:
        exact: dict[str, set[str]] = {}
        loose: dict[str, set[str]] = {}
        for ch in channels:
            if not ch.id:
                continue
            if not icons.get(ch.id):
                icons[ch.id] = ch.icon
            for name in ch.names:
                exact.setdefault(channel_key(name), set()).add(ch.id)
                loose.setdefault(channel_key(_COUNTRY_TAG_RE.sub("", name)), set()).add(ch.id)
        # Exact names first, then names without a "[UA]"-style country tag.
        for names in (exact, loose):
            for key, ids in names.items():
                if key and len(ids) == 1:
                    by_name.setdefault(key, next(iter(ids)))
    return EpgIndex(icons=icons, by_name=by_name)


async def fetch_index(urls: Sequence[str]) -> EpgIndex:
    timeout = aiohttp.ClientTimeout(total=EPG_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": USER_AGENT}) as session:
        guides = await asyncio.gather(*(_fetch_guide(session, u) for u in urls))
    index = build_index(guides)
    log.info("EPG: %d guide channels from %d/%d source(s)", len(index.icons), sum(1 for g in guides if g), len(urls))
    return index


async def _fetch_guide(session: aiohttp.ClientSession, url: str) -> list[GuideChannel]:
    reader = ChannelReader()
    received = 0
    try:
        async with session.get(url) as resp:
            resp.raise_for_status()
            async for chunk in resp.content.iter_chunked(CHUNK_BYTES):
                reader.feed(chunk)
                received += len(chunk)
                if reader.done or received >= MAX_DOWNLOAD_BYTES:
                    break
    except (aiohttp.ClientError, asyncio.TimeoutError, ParseError, zlib.error) as e:
        log.warning("EPG %s failed: %s", url, e)
        return []
    return reader.channels


def enrich(
    entries: Iterable[Entry],
    index: EpgIndex,
    aliases: Mapping[str, str],
    skip_groups: Iterable[str] = (),
) -> list[Entry]:
    """Set guide tvg-id and missing logos. Existing ids known to the guide are kept as is."""
    skip = set(skip_groups)
    alias_ids = {channel_key(name): guide_id for name, guide_id in aliases.items()}
    result = []
    for entry in entries:
        if entry.group in skip:
            result.append(entry)
            continue
        key = channel_key(entry.name)
        guide_id = entry.tvg_id if entry.tvg_id in index else alias_ids.get(key) or index.by_name.get(key)
        if guide_id is None:
            result.append(entry)
            continue
        result.append(replace(entry, tvg_id=guide_id, logo=entry.logo or index.icons.get(guide_id, "")))
    return result
