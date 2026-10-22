"""Merging source entries into channels with fallback stream URLs."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from fnmatch import fnmatchcase
from typing import Iterable

from .m3u import Entry

_ALT_SUFFIX_RE = re.compile(r"\s*[\(\[]\s*\d+\s*[\)\]]\s*$")
_QUALITY_RE = re.compile(r"\b(uhd|fhd|hd|sd|4k|hevc|h\.?26[45]|orig|50fps|1080p?|720p?)\b")
_NON_ALNUM_RE = re.compile(r"[^0-9a-zа-яёіїєґ+]+")


@dataclass(frozen=True)
class Channel:
    key: str
    name: str
    group: str
    logo: str
    tvg_id: str
    urls: tuple[str, ...]


def display_name(name: str) -> str:
    """'Интер (2)' -> 'Интер': drop the alternate-stream counter."""
    return _ALT_SUFFIX_RE.sub("", name).strip()


def channel_key(name: str) -> str:
    """Identity used to merge the same channel from different sources."""
    lowered = display_name(name).lower().replace("ё", "е")
    return _NON_ALNUM_RE.sub("", _QUALITY_RE.sub(" ", lowered))


def is_excluded(name: str, patterns: Iterable[str]) -> bool:
    """Match a channel name against exclusion patterns; '*' is a wildcard, the rest compares by channel_key."""
    key = channel_key(name)
    for pattern in patterns:
        normalized = "*".join(channel_key(part) for part in pattern.split("*"))
        if normalized.strip("*") and fnmatchcase(key, normalized):
            return True
    return False


def merge(entries: Iterable[Entry]) -> list[Channel]:
    """Group entries by channel key; the first entry wins name/group, URLs accumulate in order."""
    channels: dict[str, Channel] = {}
    for entry in entries:
        key = channel_key(entry.name) or entry.url
        existing = channels.get(key)
        if existing is None:
            channels[key] = Channel(
                key=key,
                name=display_name(entry.name) or entry.name,
                group=entry.group,
                logo=entry.logo,
                tvg_id=entry.tvg_id,
                urls=(entry.url,),
            )
            continue
        if entry.url in existing.urls:
            continue
        channels[key] = replace(
            existing,
            logo=existing.logo or entry.logo,
            tvg_id=existing.tvg_id or entry.tvg_id,
            urls=existing.urls + (entry.url,),
        )
    return list(channels.values())
