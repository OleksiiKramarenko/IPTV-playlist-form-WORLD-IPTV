#!/usr/bin/env python3
"""
IPTV Playlist Auto-Updater v4.1 (Refactored)
Workflow:
1. Run with --update:
   - Fetches .m3u links from Telegram.
   - Downloads and parses playlists.
   - Updates 'library.json' with new playlists/groups/channels.
   - NEW items are disabled by default ("enabled": false).
2. Manually edit 'library.json':
   - Change "enabled": false -> true for groups you want.
3. Run with --generate:
   - Reads 'library.json'.
   - Collects all enabled channels.
   - Checks availability.
   - Generates 'playlist.m3u'.
"""

import os
import sys
import re
import json
import time
import argparse
import requests
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Any

# ── Settings ────────────────────────────────────────────────────────────────
CHANNEL        = os.environ.get("TG_CHANNEL", "").strip().lstrip("@")
PLAYLIST_FILE  = "playlist.m3u"
LIBRARY_FILE   = "library.json"
STATS_FILE     = "channel_stats.json"

DAYS_BACK      = 3       # Fetch posts not older than N days
CHECK_WORKERS  = 50      # Threads for checking URLs
CHECK_TIMEOUT  = 4       # Timeout for checks (sec)
FAIL_LIMIT     = 2       # Remove channels after N consecutive failures (in stats)

MIN_GROUP_SIZE       = 1     # Minimum channels in a group to keep it (during parse)
ENABLE_DEDUPLICATION = True  # Deduplicate channels in the final playlist

# Extensions to filter out (VOD)
VOD_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.asf', '.webm', '.mpg', '.mpeg'}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

M3U_RE = re.compile(r'https?://[^\s"\'<>]*\.m3u8?(?:[?&][^\s"\'<>]*)?', re.IGNORECASE)

# Normalization mapping for group names
# (Removed as per request)

# ── M3U Parsing ──────────────────────────────────────────────────────────────

def parse_m3u(content: str) -> List[dict]:
    channels = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF"):
            meta = line
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or lines[j].strip().startswith("#")):
                if lines[j].strip().startswith("#EXTINF"):
                    j -= 1 
                    break
                j += 1
            
            if j < len(lines):
                url = lines[j].strip()
                if url and not url.startswith("#"):
                    name = extract_name(meta)
                    group = extract_group(meta)
                    logo = extract_logo(meta)
                    
                    if not group:
                        group = "Разное"
                    
                    # Basic VOD check
                    is_vod = any(url.lower().endswith(ext) for ext in VOD_EXTENSIONS)
                    
                    if not is_vod:
                        channels.append({
                            "name": name,
                            "group": group,
                            "logo": logo,
                            "url": url,
                            "original_meta": meta 
                        })
                i = j
        i += 1
    return channels

def extract_name(meta: str) -> str:
    m = re.search(r'tvg-name="([^"]*)"', meta, re.IGNORECASE)
    if m and m.group(1).strip():
        return m.group(1).strip()
    if "," in meta:
        return meta.split(",", 1)[-1].strip()
    return ""

def extract_group(meta: str) -> str:
    m = re.search(r'group-title="([^"]*)"', meta, re.IGNORECASE)
    return m.group(1).strip() if m else ""

def extract_logo(meta: str) -> str:
    m = re.search(r'tvg-logo="([^"]*)"', meta, re.IGNORECASE)
    return m.group(1).strip() if m else ""

# ── Check & Deduplicate ──────────────────────────────────────────────────────

def check_url(url: str) -> Optional[float]:
    try:
        start = time.monotonic()
        r = requests.head(url, timeout=CHECK_TIMEOUT, allow_redirects=True, headers=HEADERS)
        ctype = r.headers.get("Content-Type", "").lower()
        if "text/html" in ctype: return None
        if r.status_code < 400: return (time.monotonic() - start) * 1000
        
        start = time.monotonic()
        r = requests.get(url, timeout=CHECK_TIMEOUT, stream=True, headers=HEADERS)
        ctype = r.headers.get("Content-Type", "").lower()
        if "text/html" in ctype:
            r.close()
            return None
        next(r.iter_content(64), None)
        r.close()
        if r.status_code < 400: return (time.monotonic() - start) * 1000
    except Exception:
        pass
    return None

def check_batch(channels: List[dict], stats: dict) -> tuple[List[dict], dict]:
    total = len(channels)
    if total == 0: return [], stats
    print(f"🔍 Verifying {total} channels...")

    results = {}
    with ThreadPoolExecutor(max_workers=CHECK_WORKERS) as ex:
        futures = {ex.submit(check_url, ch["url"]): ch["url"] for ch in channels}
        done = 0
        for future in as_completed(futures):
            url = futures[future]
            results[url] = future.result()
            done += 1
            if done % 100 == 0: print(f"   {done}/{total}...")

    kept_channels = []
    for ch in channels:
        url = ch["url"]
        latency = results.get(url)
        entry = stats.get(url, {"fails": 0, "first_seen": datetime.now().isoformat()})
        
        if latency is not None:
            entry["fails"] = 0
            entry["latency"] = round(latency, 1)
            entry["last_ok"] = datetime.now().isoformat()
            stats[url] = entry
            kept_channels.append(ch)
        else:
            entry["fails"] = entry.get("fails", 0) + 1
            stats[url] = entry
            # Only keep if it hasn't failed too many times in history
            if entry["fails"] < FAIL_LIMIT:
                kept_channels.append(ch)
            
    print(f"   Alive: {len(kept_channels)} / {total}")
    return kept_channels, stats

def normalize_name_for_dedup(name: str) -> str:
    n = name.lower()
    n = re.sub(r'\b(hd|fhd|sd|4k|hevc|h\.265)\b', '', n)
    n = re.sub(r'[^a-zа-я0-9]', '', n)
    return n

def deduplicate(channels: List[dict], stats: dict) -> List[dict]:
    # 1. Unique URLs
    seen_urls = set()
    unique_url_channels = []
    for ch in channels:
        if ch["url"] not in seen_urls:
            seen_urls.add(ch["url"])
            unique_url_channels.append(ch)

    # 2. Best source by name
    best_channels = {} 
    for ch in unique_url_channels:
        norm_name = normalize_name_for_dedup(ch["name"])
        if not norm_name:
            best_channels[ch["url"]] = ch 
            continue
            
        if norm_name not in best_channels:
            best_channels[norm_name] = ch
        else:
            current_best = best_channels[norm_name]
            cur_lat = stats.get(current_best["url"], {}).get("latency", 9999)
            new_lat = stats.get(ch["url"], {}).get("latency", 9999)
            if new_lat < cur_lat:
                best_channels[norm_name] = ch

    return list(best_channels.values())

# ── Library Management ───────────────────────────────────────────────────────

def load_library() -> dict:
    lib = {"playlists": [], "groups": {}}
    if os.path.exists(LIBRARY_FILE):
        try:
            with open(LIBRARY_FILE, encoding="utf-8") as f:
                lib = json.load(f)
                
                # Migration: if any playlist has "groups" as dict, move them to global "groups"
                # and remove them from the playlist entry.
                if "playlists" in lib and any("groups" in pl for pl in lib["playlists"]):
                    print("🔄 Migrating library to new format...")
                    global_groups = lib.get("groups", {})
                    for pl in lib["playlists"]:
                        if "groups" in pl:
                            for g_name in pl["groups"]:
                                if g_name not in global_groups:
                                    global_groups[g_name] = False
                            del pl["groups"]
                    lib["groups"] = global_groups
                    # Save immediately after migration to avoid losing the new structure
                    # We'll save it after the check or update
                return lib
        except Exception as e:
            print(f"Error loading library: {e}")
    return lib

def save_library(lib: dict):
    # Sort groups by name for convenience
    if "groups" in lib:
        lib["groups"] = dict(sorted(lib["groups"].items()))
    # Sort playlists by date added (newest first)
    if "playlists" in lib:
        lib["playlists"].sort(key=lambda x: x.get("date_added", ""), reverse=True)
        
    with open(LIBRARY_FILE, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False, indent=4)

def load_stats() -> dict:
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_stats(stats: dict):
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

# ── Workflow: Update ─────────────────────────────────────────────────────────

def fetch_links(channel_name: str, days: int) -> List[str]:
    url = f"https://t.me/s/{channel_name}"
    print(f"📡 Reading channel: {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"Error accessing Telegram: {e}")
        return []

    html = resp.text
    posts = html.split('tgme_widget_message_wrap')
    found_links = set()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    for post in posts:
        date_m = re.search(r'datetime="([^"]+)"', post)
        if not date_m: continue
        try:
            dt = datetime.fromisoformat(date_m.group(1).replace('Z', '+00:00'))
            if dt < cutoff: continue
        except: continue

        links = M3U_RE.findall(post)
        for l in links: found_links.add(l)
            
    return list(found_links)

def update_library():
    print("--- UPDATE MODE ---")
    if not CHANNEL:
        print("Error: TG_CHANNEL not set.")
        sys.exit(1)

    lib = load_library()
    # Map of existing URLs to their playlist objects for easy access
    existing_playlists = {pl["url"]: pl for pl in lib.get("playlists", [])}
    global_groups = lib.get("groups", {})

    links = fetch_links(CHANNEL, DAYS_BACK)
    
    new_links_count = sum(1 for l in links if l not in existing_playlists)
    print(f"Found {len(links)} links, {new_links_count} are new.")

    if not links:
        print("No playlists found.")
        save_library(lib)
        return

    updated_count = 0
    
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(requests.get, url, headers=HEADERS, timeout=15): url for url in links}
        for f in as_completed(futures):
            url = futures[f]
            try:
                r = f.result()
                if r.status_code == 200:
                    channels = parse_m3u(r.text)
                    if not channels: continue
                    
                    # 1. Identify all groups in this playlist
                    found_groups = set()
                    for ch in channels:
                        found_groups.add(ch["group"])
                    
                    # 2. Add new groups to global whitelist (default disabled)
                    for g_name in found_groups:
                        if g_name not in global_groups:
                            global_groups[g_name] = False
                    
                    # 3. Get/Create playlist entry (without 'groups' field)
                    if url not in existing_playlists:
                        pl_entry = {
                            "url": url,
                            "date_added": datetime.now().isoformat(),
                            "name": f"Playlist from {datetime.now().strftime('%Y-%m-%d')}"
                        }
                        existing_playlists[url] = pl_entry
                        
                    updated_count += 1
                    print(f"Processed: {url} ({len(found_groups)} groups)")
            except Exception as e:
                print(f"Failed to download {url}: {e}")

    # Rebuild library list
    lib["playlists"] = list(existing_playlists.values())
    lib["groups"] = global_groups
    save_library(lib)
    print(f"Library updated. Total playlists: {len(lib['playlists'])}, Total groups: {len(lib['groups'])}")
    print(f"Edit '{LIBRARY_FILE}' to enable groups.")

# ── Workflow: Generate ───────────────────────────────────────────────────────

def generate_playlist():
    print("--- GENERATE MODE ---")
    lib = load_library()
    stats = load_stats()
    
    # 1. Identify enabled groups
    global_groups = lib.get("groups", {})
    enabled_groups = {g for g, is_enabled in global_groups.items() if is_enabled}
    
    if not enabled_groups:
        print(f"No groups enabled! Edit '{LIBRARY_FILE}' and set 'true' for some groups in 'groups' section.")
        sys.exit(0)

    print(f"Enabled groups: {', '.join(sorted(enabled_groups))}")
    
    candidates = []
    playlists = lib.get("playlists", [])
    
    if not playlists:
        print("No playlists in library.")
        return

    print(f"Re-fetching {len(playlists)} playlists to extract enabled channels...")

    # 2. Re-fetch and parse
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(requests.get, pl["url"], headers=HEADERS, timeout=15): pl["url"] for pl in playlists}
            
        done = 0
        for f in as_completed(futures):
            url = futures[f]
            done += 1
            if done % 10 == 0: print(f"   {done}/{len(playlists)}...")
            try:
                r = f.result()
                if r.status_code == 200:
                    channels = parse_m3u(r.text)
                    # Filter
                    for ch in channels:
                        if ch["group"] in enabled_groups:
                            candidates.append(ch)
            except Exception as e:
                # print(f"Error fetching {url}: {e}")
                pass
    
    print(f"Collected {len(candidates)} candidates from {len(playlists)} playlists.")

    # Verify
    verified, stats = check_batch(candidates, stats)
    
    # Deduplicate
    if ENABLE_DEDUPLICATION:
        verified = deduplicate(verified, stats)
    
    # Write M3U
    lines = ["#EXTM3U"]
    for ch in verified:
        # Reconstruct #EXTINF
        group_attr = f'group-title="{ch["group"]}"'
        logo_attr = f'tvg-logo="{ch["logo"]}"' if ch.get("logo") else ""
        name_attr = f'tvg-name="{ch["name"]}"'
        attrs = [x for x in [group_attr, logo_attr, name_attr] if x]
        
        extinf = f'#EXTINF:-1 {" ".join(attrs)},{ch["name"]}'
        lines.append(extinf)
        lines.append(ch["url"])
        
    with open(PLAYLIST_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
        
    save_stats(stats)
    save_library(lib) # Save in case migration happened during load
    print(f"Playlist generated: {PLAYLIST_FILE} ({len(verified)} channels)")

# ── Main Entry ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="IPTV Playlist Manager")
    parser.add_argument("--update", action="store_true", help="Fetch new playlists and update library.json")
    parser.add_argument("--generate", action="store_true", help="Generate playlist.m3u from enabled groups in library.json")
    
    args = parser.parse_args()
    
    if args.update:
        update_library()
    elif args.generate:
        generate_playlist()
    else:
        # Default behavior if no args: Help
        parser.print_help()
        print("\nExample usage:")
        print("  python3 fetch_playlist.py --update    # Step 1: Get new lists")
        print("  # ... manually edit library.json ...")
        print("  python3 fetch_playlist.py --generate  # Step 2: Make m3u")

if __name__ == "__main__":
    main()
