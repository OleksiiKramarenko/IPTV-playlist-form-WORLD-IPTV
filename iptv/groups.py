"""Mapping of source group titles onto the playlist's own groups."""

from __future__ import annotations

import re
from typing import Optional, Sequence

from .config import GroupRule

_NOISE_RE = re.compile(r"[^\w+ ]+")
_SPACES_RE = re.compile(r"\s+")


def normalize_group(title: str) -> str:
    """Lowercase and strip emoji/punctuation: '🔴 Кинозалы 🔴' -> 'кинозалы'."""
    cleaned = _NOISE_RE.sub(" ", title.lower()).replace("_", " ")
    return _SPACES_RE.sub(" ", cleaned).strip()


def classify(title: str, rules: Sequence[GroupRule], exclude: Sequence[str]) -> Optional[str]:
    """Return the target group name, or None when the title is excluded or unmatched."""
    normalized = normalize_group(title)
    if not normalized or any(pattern in normalized for pattern in exclude):
        return None
    for rule in rules:
        if any(pattern in normalized for pattern in rule.match):
            return rule.name
    return None
