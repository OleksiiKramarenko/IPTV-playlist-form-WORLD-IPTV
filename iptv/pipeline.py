"""End-to-end build: sources -> filter -> merge -> check -> pick -> guard -> write."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Sequence

from . import m3u
from .channels import Channel, is_excluded, merge
from .checker import check_urls
from .config import Config
from .epg import enrich, fetch_index
from .groups import classify
from .health import Health, load_health, pick_url, save_health, update_health
from .sources import SourceResult, fetch_sources
from .stubs import mark_shared_stubs

log = logging.getLogger(__name__)


class BuildError(RuntimeError):
    """The build produced something that must not be published."""


def run(config: Config, base_dir: Path, force: bool = False) -> int:
    """Build the playlist; returns the number of channels written."""
    results = asyncio.run(fetch_sources(config.sources))
    if not any(r.entries for r in results):
        raise BuildError("All sources failed, keeping the previous playlist")

    channels = merge(select_entries(results, config))
    log.info("Channels after filtering and merging: %d", len(channels))

    urls = [u for ch in channels for u in ch.urls]
    log.info("Checking %d stream URLs...", len(urls))
    checks = asyncio.run(check_urls(urls, config.check.timeout, config.check.workers))
    checks = mark_shared_stubs(channels, checks, config.check.stub_min_channels)
    log.info(
        "  playable now: %d/%d, stub screens: %d",
        sum(r.ok for r in checks.values()), len(checks), sum(r.stub for r in checks.values()),
    )

    health_path = base_dir / config.health_file
    health = update_health(load_health(health_path), checks, config.check.history_size)
    picked = choose_streams(channels, health, config.check.grace_fails, [g.name for g in config.groups])
    final = add_guide_info(picked, config)

    output_path = base_dir / config.output.file
    previous = m3u.count_entries(output_path.read_text(encoding="utf-8")) if output_path.exists() else 0
    if not force:
        guard(len(final), previous, config.output.min_channels, config.output.min_ratio)

    output_path.write_text(m3u.render(final, config.epg.urls), encoding="utf-8")
    save_health(health_path, health)
    log.info("Saved %s: %d channels (previous: %d)", output_path, len(final), previous)
    return len(final)


def select_entries(results: Sequence[SourceResult], config: Config) -> list[m3u.Entry]:
    """Entries of all sources (in config order) mapped onto configured groups."""
    selected: list[m3u.Entry] = []
    for result in results:
        kept = 0
        for entry in result.entries:
            if config.skip_vod_files and m3u.is_vod_file(entry.url):
                continue
            if is_excluded(entry.name, config.exclude_channels):
                continue
            group = classify(entry.group, config.groups, config.exclude_groups)
            if group is None:
                continue
            selected.append(replace(entry, group=group))
            kept += 1
        log.info("  %s: kept %d/%d entries", result.source.name, kept, len(result.entries))
    return selected


def choose_streams(
    channels: Iterable[Channel], health: Health, grace_fails: int, group_order: Sequence[str]
) -> list[m3u.Entry]:
    order = {name: i for i, name in enumerate(group_order)}
    picked = []
    for ch in channels:
        url = pick_url(ch, health, grace_fails)
        if url is not None:
            picked.append(m3u.Entry(name=ch.name, url=url, group=ch.group, logo=ch.logo, tvg_id=ch.tvg_id))
    return sorted(picked, key=lambda e: (order.get(e.group, len(order)), e.name.lower()))


def add_guide_info(entries: Sequence[m3u.Entry], config: Config) -> list[m3u.Entry]:
    """Attach EPG ids and missing logos; a failed guide download only costs the enrichment."""
    if not config.epg.urls:
        return list(entries)
    index = asyncio.run(fetch_index(config.epg.urls))
    enriched = enrich(entries, index, config.epg.aliases, config.epg.skip_groups)
    eligible = [e for e in enriched if e.group not in config.epg.skip_groups]
    log.info(
        "EPG matched %d/%d channels, logos %d/%d",
        sum(e.tvg_id in index for e in eligible), len(eligible),
        sum(bool(e.logo) for e in enriched), len(enriched),
    )
    return enriched


def guard(new_count: int, previous_count: int, min_channels: int, min_ratio: float) -> None:
    if new_count < min_channels:
        raise BuildError(f"Only {new_count} channels (< min_channels={min_channels}), keeping the previous playlist")
    if previous_count and new_count < previous_count * min_ratio:
        raise BuildError(
            f"Channel count dropped {previous_count} -> {new_count} (< {min_ratio:.0%}), keeping the previous playlist"
        )
