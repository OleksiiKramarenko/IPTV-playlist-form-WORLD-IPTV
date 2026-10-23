"""Detection of stub screens ("channel offline", "subscription required", geo-block).

Such screens are one static clip served by the provider for every channel it blocks,
so identical media bytes behind several different channels give them away.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Iterable, Mapping

from .channels import Channel
from .checker import CheckResult


def mark_shared_stubs(
    channels: Iterable[Channel], results: Mapping[str, CheckResult], min_channels: int
) -> dict[str, CheckResult]:
    """Turn playable results whose media is shared by >= min_channels channels into stubs (0 disables)."""
    if min_channels <= 0:
        return dict(results)
    owners: dict[str, set[str]] = defaultdict(set)
    for ch in channels:
        for url in ch.urls:
            result = results.get(url)
            if result is not None and result.ok and result.fingerprint:
                owners[result.fingerprint].add(ch.key)
    shared = {fp for fp, keys in owners.items() if len(keys) >= min_channels}
    return {
        url: replace(r, ok=False, stub=True, latency_ms=None) if r.ok and r.fingerprint in shared else r
        for url, r in results.items()
    }
