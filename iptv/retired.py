"""Waiting list of channels that fell out of the playlist.

A channel only the previous playlist knew about would be forgotten the moment it drops out.
Instead it waits here for keep_days and is re-checked every run, so an outage of a couple
of weeks does not lose it. retired.json is a list of
{"name", "url", "group", "logo", "tvg_id", "dropped_at": ISO-8601 UTC}.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from .channels import channel_key
from .m3u import Entry

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Retired:
    entry: Entry
    dropped_at: str


def load_retired(path: Path) -> list[Retired]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [
            Retired(
                entry=Entry(
                    name=_text(item, "name"), url=_text(item, "url"), group=_text(item, "group"),
                    logo=str(item.get("logo", "")), tvg_id=str(item.get("tvg_id", "")),
                ),
                dropped_at=_text(item, "dropped_at"),
            )
            for item in raw
        ]
    except (json.JSONDecodeError, AttributeError, KeyError, TypeError, ValueError) as e:
        log.warning("Ignoring unreadable retired file %s: %s", path, e)
        return []


def _text(item: dict, key: str) -> str:
    value = item[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"'{key}' must be a non-empty string")
    return value


def save_retired(path: Path, items: Iterable[Retired]) -> None:
    data = [{**asdict(r.entry), "dropped_at": r.dropped_at} for r in items]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def update_retired(
    waiting: Iterable[Retired],
    previous: Iterable[Entry],
    published: Iterable[Entry],
    keep_days: int,
    now: Optional[datetime] = None,
) -> list[Retired]:
    """Add channels that just dropped out; remove those that are back or waited longer than keep_days."""
    if keep_days <= 0:
        return []
    moment = now or datetime.now(timezone.utc)
    stamp = moment.isoformat(timespec="seconds")
    cutoff = (moment - timedelta(days=keep_days)).isoformat(timespec="seconds")
    candidates = list(waiting) + [Retired(entry=e, dropped_at=stamp) for e in previous]

    seen = {channel_key(e.name) for e in published}
    updated: list[Retired] = []
    for item in candidates:
        key = channel_key(item.entry.name)
        if key in seen or item.dropped_at < cutoff:
            continue
        seen.add(key)
        updated.append(item)
    return updated
