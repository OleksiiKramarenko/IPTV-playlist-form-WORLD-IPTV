#!/usr/bin/env python3
"""
Парсит публичный Telegram-канал, находит последнюю ссылку на .m3u
и сохраняет плейлист как playlist.m3u
"""

import os
import re
import sys
import requests

CHANNEL = os.environ.get("TG_CHANNEL", "").strip().lstrip("@")
if not CHANNEL:
    print("❌ Переменная TG_CHANNEL не задана.")
    sys.exit(1)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# ── 1. Загружаем страницу канала ────────────────────────────────────────────

url = f"https://t.me/s/{CHANNEL}"
print(f"📡 Загружаю канал: {url}")

resp = requests.get(url, headers=HEADERS, timeout=30)
resp.raise_for_status()
html = resp.text

# ── 2. Ищем все ссылки на .m3u / .m3u8 в тексте постов ────────────────────

# Telegram экранирует & как &amp; в HTML, учитываем оба варианта
pattern = re.compile(
    r'https?://[^\s"\'<>]*\.m3u8?(?:[?&][^\s"\'<>]*)?',
    re.IGNORECASE,
)

# Декодируем &amp; перед поиском
decoded_html = html.replace("&amp;", "&")
links = pattern.findall(decoded_html)

# Убираем дубликаты, сохраняя порядок (последний пост = последняя ссылка)
seen = set()
unique_links = []
for link in links:
    if link not in seen:
        seen.add(link)
        unique_links.append(link)

if not unique_links:
    print("❌ Ссылки на .m3u не найдены. Проверьте имя канала или формат постов.")
    sys.exit(1)

latest = unique_links[-1]
print(f"✅ Найдено ссылок: {len(unique_links)}")
print(f"🔗 Последняя: {latest}")

# ── 3. Скачиваем плейлист ──────────────────────────────────────────────────

print("⬇️  Скачиваю плейлист...")
dl = requests.get(latest, headers=HEADERS, timeout=60)
dl.raise_for_status()
content = dl.content

if b"#EXTM3U" not in content and b"#EXTINF" not in content:
    print("⚠️  Скачанный файл не похож на m3u — сохраняю всё равно.")

with open("playlist.m3u", "wb") as f:
    f.write(content)

print(f"💾 Сохранено: playlist.m3u ({len(content):,} байт)")
