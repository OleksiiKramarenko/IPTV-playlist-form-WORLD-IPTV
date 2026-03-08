# IPTV Playlist Auto-Updater via GitHub Actions

Автоматически парсит Telegram-канал, скачивает свежий плейлист и раздаёт его по постоянному URL через GitHub Pages.

## Как это работает

```
Telegram канал
      │  (каждый час)
      ▼
GitHub Actions
  └─ fetch_playlist.py  →  playlist.m3u
                                │
                          GitHub Pages
                                │
                          постоянный URL
                                │
                          Android TV 📺
```

---

## Установка — пошагово

### Шаг 1. Создать репозиторий на GitHub

1. Зайдите на [github.com](https://github.com) → **New repository**
2. Имя: `iptv-playlist` (или любое)
3. Видимость: **Public** ← обязательно, иначе GitHub Pages не работает бесплатно
4. Нажмите **Create repository**

### Шаг 2. Загрузить файлы

Загрузите в корень репозитория:
- `fetch_playlist.py`
- `playlist.m3u` (пустой файл — для первого коммита)

Файл workflow положите по пути:
```
.github/workflows/update-playlist.yml
```

Структура репозитория:
```
iptv-playlist/
├── .github/
│   └── workflows/
│       └── update-playlist.yml
├── fetch_playlist.py
└── playlist.m3u
```

### Шаг 3. Задать имя канала

1. В репозитории: **Settings → Secrets and variables → Actions → Variables**
2. Нажмите **New repository variable**
3. Name: `TG_CHANNEL`
4. Value: имя канала без `@`, например `mychannel`
5. Нажмите **Add variable**

### Шаг 4. Включить GitHub Pages

1. **Settings → Pages**
2. Source: **Deploy from a branch**
3. Branch: `main` / `master`, папка `/ (root)`
4. Нажмите **Save**
5. Через минуту страница будет доступна по адресу:
   ```
   https://ВАШ_ЛОГИН.github.io/iptv-playlist/playlist.m3u
   ```

### Шаг 5. Запустить первый раз вручную

1. Вкладка **Actions** в репозитории
2. Выберите workflow **Update IPTV Playlist**
3. Нажмите **Run workflow**
4. Дождитесь зелёной галочки ✅

### Шаг 6. Прописать URL на Android TV

В приложении (TiviMate, IPTV Smarters, GSE Smart IPTV и др.) введите:
```
https://ВАШ_ЛОГИН.github.io/iptv-playlist/playlist.m3u
```

Готово! Плейлист будет обновляться автоматически каждый час.

---

## Часто задаваемые вопросы

**Плейлист не обновляется сразу?**
GitHub Pages кэширует файлы. Добавьте `?v=1` к URL или подождите ~5 минут.

**Actions не запускается?**
В новых репозиториях Actions нужно активировать вручную: вкладка **Actions → Enable**.

**Ссылка не найдена в посте?**
Проверьте что ссылка в тексте поста заканчивается на `.m3u` или `.m3u8`. Если это сокращённая ссылка (bit.ly и т.п.) — скрипт её не распознает.

**Хочу обновлять чаще?**
В `update-playlist.yml` измените cron. Например, каждые 30 минут: `*/30 * * * *`

> ⚠️ GitHub Actions в бесплатном плане даёт 2000 минут в месяц. Запуск раз в час = ~45 мин/месяц, так что лимит вам не грозит.
