#!/usr/bin/env python3
"""
IPTV Playlist Auto-Updater v2
- Собирает ВСЕ .m3u ссылки из постов за последние 3 дня
- Скачивает все плейлисты и объединяет
- Проверяет рабочие ссылки (удаляет после 2 провалов подряд)
- Хранит статистику в channel_stats.json
- Дубли по URL убирает, по названию оставляет быстрейший
"""

import os
import re
import sys
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Настройки ────────────────────────────────────────────────────────────────
CHANNEL        = os.environ.get("TG_CHANNEL", "").strip().lstrip("@")
PLAYLIST_FILE  = "playlist.m3u"
STATS_FILE     = "channel_stats.json"
DAYS_BACK      = 3       # брать посты не старше N дней
CHECK_WORKERS  = 50      # параллельных проверок
CHECK_TIMEOUT  = 5       # секунд на одну проверку
FAIL_LIMIT     = 2       # удалять после N провалов подряд

# Группы каналов которые нужно исключить (регистр не важен)
EXCLUDE_GROUPS = {
    "новостные", "региональные", "телемагазины",
    "европа | europe", "австралия | australia", "арабские | عربي",
    "армения | հայկական", "азербайджан | azərbaycan",
    "беларусь | беларускія", "болгария | bulgaria",
    "великобритания | united kingdom", "германия | germany",
    "бразилия | brasil", "грузия | ქართული", "дания | denmark",
    "египет | egypt", "израиль | ישראלי", "индия | india",
    "испания | spain", "италия | italy", "казахстан | қазақстан",
    "канада | canada", "латвия | latvia", "литва | lithuania",
    "молдавия | moldovenească", "нидерланды | netherlands",
    "норвегия | norway", "оаэ | uae", "польша | poland",
    "португалия | portugal", "румыния | romania", "словакия | slovakia",
    "сша | usa", "таджикистан | точик", "турция | türk",
    "узбекистан | o'zbek", "украина | українські", "финляндия | finland",
    "франция | france", "хорватия | croatia", "чехия | czech republic",
    "швеция | sweden", "эстония | estonia", "южная корея | korea",
    "российские", "новости", "беларусь", "оплот", "узбекские",
    "казахстанские", "турецкие", "татарстан", "arabic", "удалить",
    "европа", "-", "сириус", "turon media", "евразия-стар",
    "цитадель-крым (vpn)", "agronet (vpn)", "квант-телеком (vpn 🇷🇺)",
    "webhost (vpn 🇷🇺)", "cloudflare inc (vpn 🇷🇺)",
    "catcast tv 🐈 not 24/7",
}
# ─────────────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

M3U_RE = re.compile(
    r'https?://[^\s"\'<>]*\.m3u8?(?:[?&][^\s"\'<>]*)?',
    re.IGNORECASE,
)


# ── Парсинг m3u ──────────────────────────────────────────────────────────────

def parse_m3u(content: str) -> list[dict]:
    channels = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF"):
            meta = line
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                url = lines[j].strip()
                if url and not url.startswith("#"):
                    if not is_excluded(meta):
                        channels.append({
                            "meta": meta,
                            "url":  url,
                            "name": extract_name(meta),
                        })
                i = j
        i += 1
    return channels


def is_excluded(meta: str) -> bool:
    """Вернуть True если group-title канала в списке исключений"""
    m = re.search(r'group-title="([^"]*)"', meta, re.IGNORECASE)
    if m:
        group = m.group(1).strip().lower()
        return group in EXCLUDE_GROUPS
    return False


def extract_name(meta: str) -> str:
    m = re.search(r'tvg-name="([^"]*)"', meta, re.IGNORECASE)
    if m and m.group(1).strip():
        return m.group(1).strip().lower()
    if "," in meta:
        return meta.split(",", 1)[-1].strip().lower()
    return ""


def channels_to_m3u(channels: list[dict]) -> str:
    lines = ["#EXTM3U"]
    for ch in channels:
        lines.append(ch["meta"])
        lines.append(ch["url"])
    return "\n".join(lines) + "\n"


# ── Статистика ───────────────────────────────────────────────────────────────

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


# ── Проверка ссылок ──────────────────────────────────────────────────────────

def check_url(url: str) -> float | None:
    """Возвращает latency в мс или None если недоступен"""
    try:
        start = time.monotonic()
        r = requests.head(url, timeout=CHECK_TIMEOUT, allow_redirects=True, headers=HEADERS)
        latency = (time.monotonic() - start) * 1000
        if r.status_code < 400:
            return latency
        start = time.monotonic()
        r = requests.get(url, timeout=CHECK_TIMEOUT, stream=True, headers=HEADERS)
        latency = (time.monotonic() - start) * 1000
        next(r.iter_content(512), None)
        if r.status_code < 400:
            return latency
    except Exception:
        pass
    return None


def check_all(channels: list[dict], stats: dict) -> tuple[list[dict], dict]:
    total = len(channels)
    print(f"🔍 Проверяю {total} ссылок (workers={CHECK_WORKERS}, timeout={CHECK_TIMEOUT}s)...")

    results: dict[str, float | None] = {}
    with ThreadPoolExecutor(max_workers=CHECK_WORKERS) as ex:
        futures = {ex.submit(check_url, ch["url"]): ch["url"] for ch in channels}
        done = 0
        for future in as_completed(futures):
            url = futures[future]
            results[url] = future.result()
            done += 1
            if done % 200 == 0 or done == total:
                alive = sum(1 for v in results.values() if v is not None)
                print(f"   {done}/{total} — живых: {alive}")

    kept, removed = [], 0
    for ch in channels:
        url = ch["url"]
        latency = results.get(url)
        entry = stats.get(url, {"fails": 0, "latency_ms": None})

        if latency is not None:
            entry["fails"] = 0
            entry["latency_ms"] = round(latency, 1)
        else:
            entry["fails"] = entry.get("fails", 0) + 1
            entry["latency_ms"] = None

        stats[url] = entry

        if entry["fails"] < FAIL_LIMIT:
            kept.append(ch)
        else:
            removed += 1

    print(f"✅ Оставлено: {len(kept)}, удалено (≥{FAIL_LIMIT} провалов подряд): {removed}")
    return kept, stats


# ── Дедупликация ─────────────────────────────────────────────────────────────

def get_latency(url: str, stats: dict) -> float:
    entry = stats.get(url, {})
    lat = entry.get("latency_ms")
    return lat if lat is not None else float("inf")


def deduplicate(channels: list[dict], stats: dict) -> list[dict]:
    seen_urls: set[str] = set()
    unique: list[dict] = []
    for ch in channels:
        if ch["url"] not in seen_urls:
            seen_urls.add(ch["url"])
            unique.append(ch)

    best: dict[str, dict] = {}
    no_name: list[dict] = []
    for ch in unique:
        name = ch["name"]
        if not name:
            no_name.append(ch)
            continue
        if name not in best:
            best[name] = ch
        else:
            if get_latency(ch["url"], stats) < get_latency(best[name]["url"], stats):
                best[name] = ch

    result = list(best.values()) + no_name
    print(f"🔄 После дедупликации: {len(result)} (убрано дублей: {len(unique) - len(result)})")
    return result


# ── Telegram ─────────────────────────────────────────────────────────────────

def fetch_links_from_channel(channel: str, days_back: int) -> list[str]:
    url = f"https://t.me/s/{channel}"
    print(f"📡 Загружаю канал: {url}")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    html = resp.text.replace("&amp;", "&")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    posts = re.split(r'(?=<div[^>]+class="[^"]*tgme_widget_message_wrap)', html)

    all_links: list[str] = []
    posts_found = 0

    for post in posts:
        date_match = re.search(r'<time[^>]+datetime="([^"]+)"', post)
        if not date_match:
            continue
        try:
            post_dt = datetime.fromisoformat(date_match.group(1))
            if post_dt.tzinfo is None:
                post_dt = post_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        if post_dt < cutoff:
            continue

        links = M3U_RE.findall(post)
        if links:
            posts_found += 1
            for link in links:
                if link not in all_links:
                    all_links.append(link)

    print(f"📋 Постов с .m3u за последние {days_back} дня(-ей): {posts_found}, ссылок: {len(all_links)}")
    return all_links


def download_all_playlists(links: list[str]) -> list[dict]:
    all_channels: list[dict] = []
    for i, link in enumerate(links, 1):
        try:
            r = requests.get(link, headers=HEADERS, timeout=60)
            r.raise_for_status()
            channels = parse_m3u(r.text)
            print(f"   [{i}/{len(links)}] {len(channels):>5} каналов <- {link}")
            all_channels.extend(channels)
        except Exception as e:
            print(f"   [{i}/{len(links)}] Ошибка: {e} <- {link}")

    print(f"Итого скачано: {len(all_channels)} каналов из {len(links)} плейлистов")
    return all_channels


# ── Главная логика ────────────────────────────────────────────────────────────

def main():
    if not CHANNEL:
        print("Переменная TG_CHANNEL не задана")
        sys.exit(1)

    stats = load_stats()
    print(f"Статистика: {len(stats)} записей")

    # 1. Текущий плейлист -> проверяем рабочие
    existing: list[dict] = []
    if os.path.exists(PLAYLIST_FILE) and os.path.getsize(PLAYLIST_FILE) > 0:
        with open(PLAYLIST_FILE, encoding="utf-8", errors="ignore") as f:
            existing = parse_m3u(f.read())
        print(f"Текущий плейлист: {len(existing)} каналов")
        existing, stats = check_all(existing, stats)
    else:
        print("Плейлист пуст - начинаем с нуля")

    # 2. Все ссылки из постов за последние DAYS_BACK дней
    links = fetch_links_from_channel(CHANNEL, DAYS_BACK)

    # 3. Скачиваем все плейлисты
    new_channels = download_all_playlists(links) if links else []

    # 4. Объединяем: проверенные рабочие + новые
    combined = existing + new_channels

    # 5. Дедупликация
    combined = deduplicate(combined, stats)

    # 6. Чистим статистику от удалённых URL
    active_urls = {ch["url"] for ch in combined}
    stats = {u: d for u, d in stats.items() if u in active_urls}

    # 7. Сохраняем
    with open(PLAYLIST_FILE, "w", encoding="utf-8") as f:
        f.write(channels_to_m3u(combined))
    save_stats(stats)

    print(f"Готово! {len(combined)} каналов -> {PLAYLIST_FILE}")


if __name__ == "__main__":
    main()