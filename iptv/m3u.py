"""Parsing and rendering of M3U playlists."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
VOD_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".asf", ".webm", ".mpg", ".mpeg")


@dataclass(frozen=True)
class Entry:
    name: str
    url: str
    group: str = ""
    logo: str = ""
    tvg_id: str = ""


def parse(text: str) -> list[Entry]:
    entries: list[Entry] = []
    pending: dict | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            pending = _parse_extinf(line)
        elif line.startswith("#EXTGRP:") and pending is not None and not pending["group"]:
            pending = {**pending, "group": line.split(":", 1)[1].strip()}
        elif line.startswith("#"):
            continue
        elif pending is not None:
            entries.append(Entry(url=line, **pending))
            pending = None
    return entries


def _parse_extinf(line: str) -> dict:
    attrs = {k.lower(): v.strip() for k, v in ATTR_RE.findall(line)}
    return {
        "name": _extract_title(line) or attrs.get("tvg-name", ""),
        "group": attrs.get("group-title", ""),
        "logo": attrs.get("tvg-logo", ""),
        "tvg_id": attrs.get("tvg-id", ""),
    }


def _extract_title(line: str) -> str:
    # The title follows the first comma after the last quoted attribute.
    last_quote = line.rfind('"')
    comma = line.find(",", last_quote + 1)
    if comma == -1:
        comma = line.find(",")
    return line[comma + 1:].strip() if comma != -1 else ""


def is_vod_file(url: str) -> bool:
    path = url.split("?", 1)[0].lower()
    return path.endswith(VOD_EXTENSIONS)


def render(entries: Iterable[Entry], epg_urls: Iterable[str] = ()) -> str:
    epg = ",".join(epg_urls)
    header = f'#EXTM3U url-tvg="{_attr(epg)}"' if epg else "#EXTM3U"
    lines = [header]
    for e in entries:
        lines.append(
            f'#EXTINF:-1 tvg-id="{_attr(e.tvg_id)}" tvg-name="{_attr(e.name)}" '
            f'tvg-logo="{_attr(e.logo)}" group-title="{_attr(e.group)}",{e.name}'
        )
        lines.append(e.url)
    return "\n".join(lines) + "\n"


def _attr(value: str) -> str:
    return value.replace('"', "'")
