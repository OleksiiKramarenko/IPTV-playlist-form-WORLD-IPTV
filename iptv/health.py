"""Per-URL check history and best-stream selection.

health.json maps stream URL -> {"history": "1101", "latency_ms": 420.5, "last_ok": ISO-8601 UTC}.
History is a string of check outcomes, oldest first ("1" = played, "0" = failed).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

from .channels import Channel
from .checker import CheckResult

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UrlHealth:
    history: str = ""
    latency_ms: Optional[float] = None
    last_ok: Optional[str] = None

    @property
    def success_rate(self) -> float:
        return self.history.count("1") / len(self.history) if self.history else 0.0

    @property
    def consecutive_fails(self) -> int:
        return len(self.history) - len(self.history.rstrip("0"))


Health = Mapping[str, UrlHealth]


def load_health(path: Path) -> dict[str, UrlHealth]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {
            url: UrlHealth(
                history=str(item.get("history", "")),
                latency_ms=item.get("latency_ms"),
                last_ok=item.get("last_ok"),
            )
            for url, item in raw.items()
        }
    except (json.JSONDecodeError, AttributeError, TypeError) as e:
        log.warning("Ignoring unreadable health file %s: %s", path, e)
        return {}


def save_health(path: Path, health: Health) -> None:
    data = {
        url: {"history": h.history, "latency_ms": h.latency_ms, "last_ok": h.last_ok}
        for url, h in sorted(health.items())
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def update_health(
    previous: Health,
    results: Mapping[str, CheckResult],
    history_size: int,
    now: Optional[datetime] = None,
) -> dict[str, UrlHealth]:
    """Append this run's outcomes. URLs not checked this run are dropped."""
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    updated: dict[str, UrlHealth] = {}
    for url, result in results.items():
        old = previous.get(url, UrlHealth())
        history = (old.history + ("1" if result.ok else "0"))[-history_size:]
        updated[url] = UrlHealth(
            history=history,
            latency_ms=result.latency_ms if result.ok else old.latency_ms,
            last_ok=stamp if result.ok else old.last_ok,
        )
    return updated


def pick_url(channel: Channel, health: Health, grace_fails: int) -> Optional[str]:
    """Best URL for a channel: alive now first, then recently reliable within the grace window."""

    def rank(url: str) -> tuple[float, float]:
        h = health[url]
        return (-h.success_rate, h.latency_ms if h.latency_ms is not None else float("inf"))

    known = [u for u in channel.urls if u in health]
    alive = [u for u in known if health[u].history.endswith("1")]
    if alive:
        return min(alive, key=rank)

    in_grace = [
        u for u in known
        if "1" in health[u].history and health[u].consecutive_fails <= grace_fails
    ]
    return min(in_grace, key=rank) if in_grace else None
