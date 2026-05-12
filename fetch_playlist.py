#!/usr/bin/env python3
"""IPTV Playlist Builder — source: iptv-org/iptv API"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

CONFIG_FILE = Path("config.json")
API_BASE = "https://iptv-org.github.io/api"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; IPTV-Builder/1.0)"}


def load_config() -> dict:
    with open(CONFIG_FILE) as f:
        return json.load(f)


async def fetch_json(session: aiohttp.ClientSession, url: str):
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as r:
        r.raise_for_status()
        return await r.json(content_type=None)


async def is_alive(session: aiohttp.ClientSession, url: str, timeout: int) -> bool:
    t = aiohttp.ClientTimeout(total=timeout)
    try:
        async with session.head(url, timeout=t, allow_redirects=True) as r:
            if r.status < 400:
                return True
    except Exception:
        pass
    try:
        async with session.get(url, timeout=t) as r:
            return r.status < 400
    except Exception:
        return False


async def check_streams(urls: list[str], timeout: int, workers: int) -> set[str]:
    sem = asyncio.Semaphore(workers)
    alive: set[str] = set()

    async def check(session: aiohttp.ClientSession, url: str):
        async with sem:
            if await is_alive(session, url, timeout):
                alive.add(url)

    connector = aiohttp.TCPConnector(limit=workers, ssl=False)
    async with aiohttp.ClientSession(connector=connector, headers=HEADERS) as session:
        await asyncio.gather(*[check(session, url) for url in urls])

    return alive


async def main():
    cfg = load_config()

    f = cfg.get("filters", {})
    countries       = {c.upper() for c in f.get("countries", [])}
    categories      = {c.lower() for c in f.get("categories", [])}
    languages       = {l.lower() for l in f.get("languages", [])}
    exclude_ids     = set(f.get("exclude_channel_ids", []))
    exclude_nsfw    = f.get("exclude_nsfw", True)

    out             = cfg.get("output", {})
    playlist_file   = out.get("file", "playlist.m3u")
    group_by        = out.get("group_by", "category")
    include_logos   = out.get("include_logos", True)

    epg_cfg         = cfg.get("epg", {})
    epg_enabled     = epg_cfg.get("enabled", True)
    epg_url_manual  = epg_cfg.get("url", "")

    chk             = cfg.get("stream_check", {})
    check_enabled   = chk.get("enabled", True)
    check_timeout   = chk.get("timeout", 8)
    check_workers   = chk.get("workers", 50)

    # ── Fetch API ────────────────────────────────────────────────────────────
    print("Fetching iptv-org API...")
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector, headers=HEADERS) as session:
        tasks = [
            fetch_json(session, f"{API_BASE}/channels.json"),
            fetch_json(session, f"{API_BASE}/streams.json"),
        ]
        if epg_enabled:
            tasks.append(fetch_json(session, f"{API_BASE}/guides.json"))
        results = await asyncio.gather(*tasks)

    channels_raw = results[0]
    streams_raw  = results[1]
    guides_raw   = results[2] if epg_enabled else []

    print(f"  Channels: {len(channels_raw)}, Streams: {len(streams_raw)}, Guides: {len(guides_raw)}")

    # ── Index streams ─────────────────────────────────────────────────────────
    # channel_id -> first working stream URL
    streams_index: dict[str, str] = {}
    for s in streams_raw:
        cid = s.get("channel") or ""
        url = s.get("url") or ""
        if cid and url and not s.get("is_broken") and cid not in streams_index:
            streams_index[cid] = url

    # ── Index EPG ─────────────────────────────────────────────────────────────
    # channel_id -> epg xml url
    epg_index: dict[str, str] = {}
    if epg_enabled:
        for g in guides_raw:
            cid = g.get("channel") or ""
            url = g.get("url") or ""
            if cid and url and cid not in epg_index:
                epg_index[cid] = url

    # ── Filter channels ───────────────────────────────────────────────────────
    matched = []
    for ch in channels_raw:
        cid = ch.get("id", "")

        if cid in exclude_ids:
            continue
        if exclude_nsfw and ch.get("is_nsfw"):
            continue
        if cid not in streams_index:
            continue

        ch_country = (ch.get("country") or "").upper()
        ch_langs   = {(l or "").lower() for l in (ch.get("languages") or [])}
        ch_cats    = {(c or "").lower() for c in (ch.get("categories") or [])}

        if countries  and ch_country not in countries:
            continue
        if categories and not (ch_cats & categories):
            continue
        if languages  and not (ch_langs & languages):
            continue

        matched.append(ch)

    print(f"Channels after filters: {len(matched)}")

    # ── Check stream availability ─────────────────────────────────────────────
    channel_url = {ch["id"]: streams_index[ch["id"]] for ch in matched}

    if check_enabled and matched:
        all_urls = list(channel_url.values())
        print(f"Checking {len(all_urls)} streams (workers={check_workers}, timeout={check_timeout}s)...")
        alive = await check_streams(all_urls, check_timeout, check_workers)
        matched = [ch for ch in matched if channel_url[ch["id"]] in alive]
        print(f"  Alive: {len(matched)}/{len(all_urls)}")

    print(f"Final playlist: {len(matched)} channels")

    # ── Collect EPG header URLs ───────────────────────────────────────────────
    if epg_url_manual:
        header_epg = [epg_url_manual]
    elif epg_enabled:
        seen: set[str] = set()
        header_epg = []
        for ch in matched:
            url = epg_index.get(ch["id"])
            if url and url not in seen:
                seen.add(url)
                header_epg.append(url)
    else:
        header_epg = []

    # ── Group helper ──────────────────────────────────────────────────────────
    def get_group(ch: dict) -> str:
        if group_by == "category":
            cats = ch.get("categories") or []
            return cats[0].title() if cats else "General"
        if group_by == "country":
            return (ch.get("country") or "Other").upper()
        return "IPTV"

    matched.sort(key=lambda c: (get_group(c), (c.get("name") or "")))

    # ── Build M3U ─────────────────────────────────────────────────────────────
    lines: list[str] = []
    tvg_urls = " ".join(f'url-tvg="{u}"' for u in header_epg[:20])
    lines.append(f"#EXTM3U {tvg_urls}\n")

    for ch in matched:
        cid   = ch["id"]
        name  = ch.get("name") or cid
        logo  = (ch.get("logo") or "") if include_logos else ""
        group = get_group(ch)
        url   = channel_url[cid]

        lines.append(
            f'#EXTINF:-1 tvg-id="{cid}" tvg-name="{name}" tvg-logo="{logo}" group-title="{group}",{name}'
        )
        lines.append(url)
        lines.append("")

    Path(playlist_file).write_text("\n".join(lines), encoding="utf-8")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"Saved: {playlist_file} ({ts})")


if __name__ == "__main__":
    asyncio.run(main())