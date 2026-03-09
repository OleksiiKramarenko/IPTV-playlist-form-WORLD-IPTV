#!/usr/bin/env python3
"""
IPTV Playlist Auto-Updater v1.1
- Парсит публичный Telegram-канал
- Находит последний пост с .m3u ссылками
- Скачивает все плейлисты из этого поста и объединяет их
- Сохраняет как playlist.m3u
"""

import os
import re
import sys
import requests

# ── Настройки ───────────────────────────────────────────────────────────────
CHANNEL       = os.environ.get("TG_CHANNEL", "").strip().lstrip("@")
PLAYLIST_FILE = "playlist.m3u"
# ────────────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

M3U_PATTERN = re.compile(
    r'https?://[^\s"\'<>]*\.m3u8?(?:[?&][^\s"\'<>]*)?',
    re.IGNORECASE,
)


def get_links_from_last_post(channel: str) -> list[str]:
    """
    Загружает страницу канала, разбивает на посты,
    возвращает все .m3u ссылки из последнего поста который их содержит.
    """
    url = f"https://t.me/s/{channel}"
    print(f"📡 Загружаю канал: {url}")

    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    html = resp.text.replace("&amp;", "&")

    # Telegram рендерит каждый пост в блок <div class="tgme_widget_message_wrap ...">
    # Разбиваем HTML по этому разделителю — каждый элемент соответствует одному посту
    posts = re.split(r'(?=<div[^>]+class="[^"]*tgme_widget_message_wrap)', html)

    # Идём с конца — ищем последний пост с .m3u ссылкой
    for post in reversed(posts):
        links = list(dict.fromkeys(M3U_PATTERN.findall(post)))
        if links:
            print(f"✅ Найден пост с {len(links)} ссылкой(-ами):")
            for link in links:
                print(f"   🔗 {link}")
            return links

    print("❌ Ни в одном посте не найдено ссылок на .m3u")
    return []


def download_playlist(url: str) -> str | None:
    """Скачать один плейлист, вернуть текст или None при ошибке"""
    try:
        r = requests.get(url, headers=HEADERS, timeout=60)
        r.raise_for_status()
        content = r.text
        if "#EXTM3U" not in content and "#EXTINF" not in content:
            print(f"   ⚠️  Файл не похож на m3u: {url}")
        return content
    except Exception as e:
        print(f"   ❌ Ошибка скачивания {url}: {e}")
        return None


def merge_playlists(texts: list[str]) -> str:
    """Объединить несколько m3u в один, убрать дубли по URL"""
    seen_urls: set[str] = set()
    result_lines = ["#EXTM3U"]

    for text in texts:
        lines = text.splitlines()
        i = 0
        # Пропускаем заголовок #EXTM3U каждого файла
        while i < len(lines) and not lines[i].strip().startswith("#EXTINF"):
            i += 1
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("#EXTINF"):
                meta = line
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines):
                    url = lines[j].strip()
                    if url and not url.startswith("#") and url not in seen_urls:
                        seen_urls.add(url)
                        result_lines.append(meta)
                        result_lines.append(url)
                    i = j
            i += 1

    return "\n".join(result_lines) + "\n"


def main():
    if not CHANNEL:
        print("❌ Переменная TG_CHANNEL не задана")
        sys.exit(1)

    # 1. Найти ссылки в последнем посте
    links = get_links_from_last_post(CHANNEL)
    if not links:
        sys.exit(1)

    # 2. Скачать все плейлисты из поста
    texts = []
    for i, link in enumerate(links, 1):
        print(f"⬇️  [{i}/{len(links)}] Скачиваю {link}")
        text = download_playlist(link)
        if text:
            texts.append(text)

    if not texts:
        print("❌ Не удалось скачать ни один плейлист")
        sys.exit(1)

    # 3. Объединяем и убираем дубли по URL
    merged = merge_playlists(texts)
    channel_count = merged.count("#EXTINF")

    # 4. Сохраняем
    with open(PLAYLIST_FILE, "w", encoding="utf-8") as f:
        f.write(merged)

    print(f"💾 Сохранено: {PLAYLIST_FILE} ({channel_count} каналов)")


if __name__ == "__main__":
    main()