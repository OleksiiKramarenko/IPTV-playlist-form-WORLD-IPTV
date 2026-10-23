from datetime import datetime, timedelta, timezone

from iptv.m3u import Entry
from iptv.retired import Retired, load_retired, save_retired, update_retired

NOW = datetime(2026, 1, 31, tzinfo=timezone.utc)
STAMP = NOW.isoformat(timespec="seconds")


def entry(name: str, url: str = "") -> Entry:
    return Entry(name=name, url=url or f"https://{name}", group="G")


def days_ago(days: int) -> str:
    return (NOW - timedelta(days=days)).isoformat(timespec="seconds")


def test_dropped_channels_wait_and_returned_or_expired_ones_leave():
    waiting = (
        Retired(entry("old"), dropped_at=days_ago(10)),
        Retired(entry("back"), dropped_at=days_ago(5)),
        Retired(entry("expired"), dropped_at=days_ago(31)),
    )
    previous = [entry("kept"), entry("lost"), entry("Back HD", "https://back2")]
    published = [entry("kept"), entry("Back HD", "https://back2")]

    updated = update_retired(waiting, previous, published, keep_days=30, now=NOW)

    assert updated == [
        Retired(entry("old"), dropped_at=days_ago(10)),
        Retired(entry("lost"), dropped_at=STAMP),
    ]


def test_channel_dropped_again_keeps_its_first_drop_date():
    waiting = (Retired(entry("x", "https://x-old"), dropped_at=days_ago(3)),)

    updated = update_retired(waiting, [entry("X HD", "https://x-new")], [], keep_days=30, now=NOW)

    assert updated == [Retired(entry("x", "https://x-old"), dropped_at=days_ago(3))]


def test_zero_keep_days_disables_waiting():
    assert update_retired((), [entry("lost")], [], keep_days=0, now=NOW) == []


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "retired.json"
    items = [Retired(Entry(name="Канал", url="https://c", group="G", logo="https://l", tvg_id="c1"), dropped_at=STAMP)]

    save_retired(path, items)

    assert load_retired(path) == items


def test_load_tolerates_missing_and_corrupt_files(tmp_path):
    assert load_retired(tmp_path / "missing.json") == []
    broken = tmp_path / "broken.json"
    broken.write_text('[{"name": 1}]', encoding="utf-8")
    assert load_retired(broken) == []
