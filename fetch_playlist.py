#!/usr/bin/env python3
"""
IPTV Playlist Auto-Updater v3
- Собирает .m3u ссылки из постов Telegram
- Фильтрует по ключевым словам (exclude.txt) в НАЗВАНИИ и ГРУППЕ
- Проверяет доступность каналов перед добавлением
- Хранит статистику стабильности каналов
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
EXCLUDE_FILE   = "exclude.txt"

DAYS_BACK      = 3       # Брать посты не старше N дней
CHECK_WORKERS  = 50      # Количество потоков для проверки
CHECK_TIMEOUT  = 4       # Тайм-аут проверки (сек)
FAIL_LIMIT     = 2       # Удалять старые каналы после N провалов подряд

MIN_GROUP_SIZE       = 10    # Если в группе меньше N каналов - удаляем всю группу
ENABLE_DEDUPLICATION = False  # 1 = включить умную дедупликацию, 0 = выключить (оставить все дубли)

# Нормализация названий групп (синонимы -> единое название)
GROUP_MAPPING = {
    "кино": "Cinema",
    "фильмы": "Cinema",
    "movies": "Cinema",
    "фильм": "Cinema",
    "cinema": "Cinema",
    "kino": "Cinema",
    "kinozal": "Cinema",
    "кинозал": "Cinema",
    "serial": "Cinema",
    "сериал": "Cinema",
    "melodrama": "Cinema",
    "мелодрама": "Cinema",

    "спорт": "Sports",
    "sport": "Sports",
    "football": "Sports",
    "soccer": "Sports",
    "футбол": "Sports",
    "match": "Sports",
    "ufc": "Sports",

    "детские": "Kids",
    "kids": "Kids",
    "мультфильмы": "Kids",
    "cartoons": "Kids",
    "animation": "Kids",
    "children": "Kids",
    "діти": "Kids",
    "мульт": "Kids",
    "baby": "Kids",

    "музыка": "Music",
    "music": "Music",
    "clips": "Music",
    "radio": "Music",
    "радио": "Music",
    "музон": "Music",

    "новости": "News",
    "news": "News",
    "info": "News",

    "познавательные": "Science",
    "science": "Science",
    "discovery": "Science",
    "history": "Science",
    "education": "Science",
    "nature": "Science",

    "развлекательные": "Entertainment",
    "entertainment": "Entertainment",
    "hobby": "Entertainment",
    "хобби": "Entertainment",
    "юмор": "Entertainment",
    "humor": "Entertainment",
    "relax": "Entertainment",

    "взрослые": "Adult",
    "xxx": "Adult",
    "adult": "Adult",
    "18+": "Adult"
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

M3U_RE = re.compile(r'https?://[^\s"\'<>]*\.m3u8?(?:[?&][^\s"\'<>]*)?', re.IGNORECASE)

# ── Загрузка исключений ──────────────────────────────────────────────────────

def load_excludes():
    normal_keywords = set()
    strict_keywords = set()
    
    if os.path.exists(EXCLUDE_FILE):
        with open(EXCLUDE_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.split("#")[0].strip().lower()
                if not line:
                    continue
                
                if line.startswith("^"):
                    # Строгий режим (только целое слово)
                    strict_keywords.add(line[1:]) # убираем ^
                else:
                    # Обычный режим (частичное совпадение)
                    normal_keywords.add(line)
                    
    return list(normal_keywords), list(strict_keywords)

EXCLUDE_KEYWORDS, STRICT_KEYWORDS = load_excludes()

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
            # Ищем следующую строку с ссылкой
            while j < len(lines) and (not lines[j].strip() or lines[j].strip().startswith("#")):
                 # Если наткнулись на следующий EXTINF, значит ссылка отсутствовала
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
                    
                    # Нормализуем группу сразу при парсинге
                    normalized_group = normalize_group(group)
                    
                    if not is_excluded(name, normalized_group):
                        channels.append({
                            "meta": meta, # Сохраняем оригинал meta для совместимости, но будем пересобирать
                            "url":  url,
                            "name": name,
                            "group": normalized_group,
                            "logo": logo
                        })
                i = j
        i += 1
    return channels

def extract_name(meta: str) -> str:
    # Пробуем tvg-name
    m = re.search(r'tvg-name="([^"]*)"', meta, re.IGNORECASE)
    if m and m.group(1).strip():
        return m.group(1).strip()
    # Иначе берем все после запятой
    if "," in meta:
        return meta.split(",", 1)[-1].strip()
    return ""

def extract_group(meta: str) -> str:
    m = re.search(r'group-title="([^"]*)"', meta, re.IGNORECASE)
    return m.group(1).strip() if m else ""

def extract_logo(meta: str) -> str:
    m = re.search(r'tvg-logo="([^"]*)"', meta, re.IGNORECASE)
    return m.group(1).strip() if m else ""

def normalize_group(group_name: str) -> str:
    """Приводит разные названия групп к единому стандарту"""
    if not group_name:
        return ""
    lower_name = group_name.lower().strip()
    
    # Проверяем полное совпадение с ключом
    if lower_name in GROUP_MAPPING:
        return GROUP_MAPPING[lower_name]
    
    # Проверяем частичное совпадение (если ключ входит в название группы)
    for key, val in GROUP_MAPPING.items():
        if key in lower_name:
            return val
            
    return group_name # Возвращаем как есть, если нет совпадений

def is_excluded(name: str, group: str) -> bool:
    """
    Проверяем и название, и группу.
    1. Обычные ключевые слова (частичное совпадение)
    2. Строгие ключевые слова (целое слово, \bword\b)
    """
    text_to_check = (name + " " + group).lower()
    
    # 1. Обычный поиск (подстрока)
    for kw in EXCLUDE_KEYWORDS:
        if kw in text_to_check:
            return True
            
    # 2. Строгий поиск (целое слово)
    if STRICT_KEYWORDS:
        # Используем regex для поиска целых слов
        # Экранируем keywords на всякий случай
        pattern = r'\b(' + '|'.join(map(re.escape, STRICT_KEYWORDS)) + r')\b'
        if re.search(pattern, text_to_check):
            return True
            
    return False

def channels_to_m3u(channels: list[dict]) -> str:
    lines = ["#EXTM3U"]
    for ch in channels:
        # Пересобираем #EXTINF строку с правильной группой и логотипом
        # Формат: #EXTINF:-1 group-title="Cinema" tvg-logo="http..." tvg-name="Channel",Channel Name
        
        group_attr = f'group-title="{ch["group"]}"' if ch["group"] else ""
        logo_attr = f'tvg-logo="{ch["logo"]}"' if ch.get("logo") else ""
        name_attr = f'tvg-name="{ch["name"]}"'
        
        # Собираем атрибуты
        attrs = [x for x in [group_attr, logo_attr, name_attr] if x]
        extinf = f'#EXTINF:-1 {" ".join(attrs)},{ch["name"]}'
        
        lines.append(extinf)
        lines.append(ch["url"])
    return "\n".join(lines) + "\n"

# ── Фильтрация групп ─────────────────────────────────────────────────────────

def filter_small_groups(channels_lists: list[list[dict]]) -> list[list[dict]]:
    """
    Принимает несколько списков каналов (например, [old_channels, new_channels]).
    Считает статистику групп по ВСЕМ спискам сразу.
    Возвращает очищенные списки, где удалены каналы из мелких групп.
    """
    # 1. Считаем статистику по всем каналам
    group_counts = {}
    for ch_list in channels_lists:
        for ch in ch_list:
            # Группы уже нормализованы при парсинге
            g_key = ch["group"]
            if not g_key:
                g_key = "undefined" # Группируем каналы без группы вместе
            group_counts[g_key] = group_counts.get(g_key, 0) + 1
            
    print(f"📊 Найдено {len(group_counts)} групп. Удаляем те, где < {MIN_GROUP_SIZE} каналов...")
    
    # 2. Определяем разрешенные группы
    valid_groups = {g for g, count in group_counts.items() if count >= MIN_GROUP_SIZE}
    
    # 3. Фильтруем каждый список
    filtered_lists = []
    removed_count = 0
    
    for ch_list in channels_lists:
        new_list = []
        for ch in ch_list:
            g_key = ch["group"]
            if not g_key: g_key = "undefined"
            
            if g_key in valid_groups:
                new_list.append(ch)
            else:
                removed_count += 1
        filtered_lists.append(new_list)
        
    print(f"✂️  Удалено {removed_count} каналов из мелких групп.")
    return filtered_lists

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
    try:
        start = time.monotonic()
        # Сначала HEAD (быстро)
        r = requests.head(url, timeout=CHECK_TIMEOUT, allow_redirects=True, headers=HEADERS)
        
        # Проверяем Content-Type (отсекаем HTML заглушки)
        ctype = r.headers.get("Content-Type", "").lower()
        if "text/html" in ctype:
            return None
            
        if r.status_code < 400:
            return (time.monotonic() - start) * 1000
        
        # Если HEAD не прошел (некоторые сервера блокируют), пробуем GET stream
        start = time.monotonic()
        r = requests.get(url, timeout=CHECK_TIMEOUT, stream=True, headers=HEADERS)
        
        # Проверяем Content-Type снова
        ctype = r.headers.get("Content-Type", "").lower()
        if "text/html" in ctype:
            r.close()
            return None
            
        # Читаем пару байт
        next(r.iter_content(64), None)
        r.close()
        
        if r.status_code < 400:
            return (time.monotonic() - start) * 1000
    except Exception:
        pass
    return None

def check_batch(channels: list[dict], stats: dict, is_new_batch: bool = False) -> tuple[list[dict], dict]:
    """
    is_new_batch=True: Жесткая проверка. Если не работает - сразу удаляем.
    is_new_batch=False: Мягкая проверка. Удаляем только если fails >= FAIL_LIMIT.
    """
    total = len(channels)
    if total == 0:
        return [], stats
        
    print(f"🔍 Проверка {total} ссылок (новые={is_new_batch})...")

    results = {}
    with ThreadPoolExecutor(max_workers=CHECK_WORKERS) as ex:
        futures = {ex.submit(check_url, ch["url"]): ch["url"] for ch in channels}
        done = 0
        for future in as_completed(futures):
            url = futures[future]
            results[url] = future.result()
            done += 1
            if done % 100 == 0:
                print(f"   {done}/{total}...")

    kept_channels = []
    
    for ch in channels:
        url = ch["url"]
        latency = results.get(url)
        
        # Обновляем статистику
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
            
            # Логика удаления
            if is_new_batch:
                # Новые каналы должны работать сразу
                pass 
            else:
                # Старые каналы имеют право на ошибку
                if entry["fails"] < FAIL_LIMIT:
                    kept_channels.append(ch)

    print(f"   Результат: {len(kept_channels)} из {total} (удалено: {total - len(kept_channels)})")
    return kept_channels, stats

# ── Дедупликация ─────────────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    """Упрощает имя для сравнения (убирает HD, FHD, спецсимволы)"""
    n = name.lower()
    n = re.sub(r'\b(hd|fhd|sd|4k|hevc|h\.265)\b', '', n)
    n = re.sub(r'[^a-zа-я0-9]', '', n)
    return n

def deduplicate(channels: list[dict], stats: dict) -> list[dict]:
    # 1. Удаляем полные дубликаты URL
    seen_urls = set()
    unique_url_channels = []
    for ch in channels:
        if ch["url"] not in seen_urls:
            seen_urls.add(ch["url"])
            unique_url_channels.append(ch)

    # 2. Выбираем лучший источник для каждого канала
    best_channels = {} # Key: normalized_name
    
    for ch in unique_url_channels:
        norm_name = normalize_name(ch["name"])
        if not norm_name:
            # Если имя не удалось нормализовать, оставляем как есть
            best_channels[ch["url"]] = ch 
            continue
            
        if norm_name not in best_channels:
            best_channels[norm_name] = ch
        else:
            # Сравниваем с текущим лучшим
            current_best = best_channels[norm_name]
            cur_lat = stats.get(current_best["url"], {}).get("latency", 9999)
            new_lat = stats.get(ch["url"], {}).get("latency", 9999)
            
            # Если новый быстрее - берем его
            if new_lat < cur_lat:
                best_channels[norm_name] = ch

    return list(best_channels.values())

# ── Telegram ─────────────────────────────────────────────────────────────────

def fetch_links(channel_name: str, days: int) -> list[str]:
    url = f"https://t.me/s/{channel_name}"
    print(f"📡 Читаем канал: {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"Ошибка доступа к Telegram: {e}")
        return []

    html = resp.text
    # Простая разбивка на посты (грубая, но работает для s/ каналов)
    posts = html.split('tgme_widget_message_wrap')
    
    found_links = set()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    for post in posts:
        # Ищем дату
        date_m = re.search(r'datetime="([^"]+)"', post)
        if not date_m: continue
        
        try:
            dt = datetime.fromisoformat(date_m.group(1).replace('Z', '+00:00'))
            if dt < cutoff: continue
        except: continue

        # Ищем ссылки
        links = M3U_RE.findall(post)
        for l in links:
            found_links.add(l)
            
    print(f"Найдено {len(found_links)} уникальных ссылок на плейлисты за {days} дн.")
    return list(found_links)

def download_playlists(links: list[str]) -> list[dict]:
    all_ch = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(requests.get, url, headers=HEADERS, timeout=15): url for url in links}
        for f in as_completed(futures):
            try:
                r = f.result()
                if r.status_code == 200:
                    ch = parse_m3u(r.text)
                    all_ch.extend(ch)
            except:
                pass
    return all_ch

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not CHANNEL:
        print("Ошибка: Не задана переменная TG_CHANNEL")
        sys.exit(1)
        
    print(f"Загрузка исключений: {len(EXCLUDE_KEYWORDS)} слов")
    stats = load_stats()
    
    # 1. Загружаем локальный плейлист
    local_channels = []
    if os.path.exists(PLAYLIST_FILE):
        with open(PLAYLIST_FILE, encoding="utf-8", errors="ignore") as f:
            local_channels = parse_m3u(f.read())
    print(f"Локально найдено: {len(local_channels)} каналов")
    
    # 2. Ищем новые в Telegram
    links = fetch_links(CHANNEL, DAYS_BACK)
    new_channels_raw = []
    if links:
        new_channels_raw = download_playlists(links)
        print(f"Скачано новых каналов (сырых): {len(new_channels_raw)}")

    # 3. Фильтруем новые каналы по именам (exclude.txt)
    # Старые не фильтруем тут, т.к. предполагаем что они уже прошли фильтр раньше
    # (или пользователь удалил их руками, а мы не хотим удалять лишнее)
    new_channels_filtered = [
        ch for ch in new_channels_raw 
        if not is_excluded(ch["name"], ch["group"])
    ]
    print(f"Новых после фильтра имен: {len(new_channels_filtered)}")

    # 4. Фильтруем мелкие группы
    # Передаем ОБА списка, чтобы считать статистику по сумме (старые + новые)
    # Это спасет группу, если она была маленькой, но пришли новые каналы
    local_channels, new_channels_filtered = filter_small_groups([local_channels, new_channels_filtered])
    
    # 5. Проверяем доступность
    # Сначала старые (мягкая проверка)
    print(f"--- Проверка существующих каналов ---")
    verified_old, stats = check_batch(local_channels, stats, is_new_batch=False)
    
    # Потом новые (жесткая проверка)
    print(f"--- Проверка НОВЫХ каналов ---")
    verified_new, stats = check_batch(new_channels_filtered, stats, is_new_batch=True)
    
    # 6. Объединяем
    final_list = verified_old + verified_new
        
    # 7. Дедупликация (опционально)
    if ENABLE_DEDUPLICATION:
        print("Запуск дедупликации...")
        final_list = deduplicate(final_list, stats)
    else:
        print("Дедупликация пропущена (ENABLE_DEDUPLICATION=False)")
    
    # Чистим статистику от мусора (удаляем URL которых нет в финальном списке)
    final_urls = {ch["url"] for ch in final_list}
    stats = {k:v for k,v in stats.items() if k in final_urls}
    
    with open(PLAYLIST_FILE, "w", encoding="utf-8") as f:
        f.write(channels_to_m3u(final_list))
    
    save_stats(stats)
    print(f"Готово! Всего каналов: {len(final_list)}")

if __name__ == "__main__":
    main()
