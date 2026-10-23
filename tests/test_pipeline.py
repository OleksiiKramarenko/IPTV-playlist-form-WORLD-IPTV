import pytest

from iptv import m3u, pipeline
from iptv.checker import CheckResult
from iptv.config import parse_config
from iptv.epg import EpgIndex
from iptv.health import UrlHealth, load_health
from iptv.sources import SourceResult

CONFIG = parse_config({
    "sources": [{"name": "a", "url": "https://src/a.m3u"}, {"name": "b", "url": "https://src/b.m3u"}],
    "groups": [{"name": "Основные", "match": ["основные"]}, {"name": "Кинозалы", "match": ["кинозал"]}],
    "exclude_groups": ["vpn"],
    "exclude_channels": ["Россия 1"],
    "output": {"min_channels": 1, "min_ratio": 0.5},
    "epg": {"urls": ["https://epg/x.xml"], "aliases": {"Первый": "pervy"}, "skip_groups": ["Кинозалы"]},
})
GUIDE = EpgIndex(icons={"pervy": "https://logo/pervy.png", "kino1": "https://logo/k.png"}, by_name={"кинозал1": "kino1"})

ENTRIES_A = (
    m3u.Entry(name="Первый", url="https://a/1", group="Основные"),
    m3u.Entry(name="Кинозал 1", url="https://a/k1", group="🔴 Кинозалы 🔴"),
    m3u.Entry(name="Фильм", url="https://a/film.mp4", group="Основные"),
    m3u.Entry(name="Скрытый", url="https://a/vpn", group="Кинозалы VPN"),
)
ENTRIES_B = (
    m3u.Entry(name="Первый HD", url="https://b/1", group="Основные"),
    m3u.Entry(name="Актёр", url="https://b/x", group="Брюс Уиллис"),
    m3u.Entry(name="Россия 1 HD", url="https://b/r1", group="Основные"),
)


def source_results(*entry_sets):
    return [SourceResult(source=s, entries=e) for s, e in zip(CONFIG.sources, entry_sets)]


def test_select_entries_filters_vod_excluded_channels_and_groups():
    selected = pipeline.select_entries(source_results(ENTRIES_A, ENTRIES_B), CONFIG)

    assert [(e.name, e.group) for e in selected] == [
        ("Первый", "Основные"), ("Кинозал 1", "Кинозалы"), ("Первый HD", "Основные"),
    ]


def test_guard():
    pipeline.guard(new_count=60, previous_count=100, min_channels=10, min_ratio=0.5)
    pipeline.guard(new_count=10, previous_count=0, min_channels=10, min_ratio=0.5)
    with pytest.raises(pipeline.BuildError, match="min_channels"):
        pipeline.guard(new_count=9, previous_count=0, min_channels=10, min_ratio=0.5)
    with pytest.raises(pipeline.BuildError, match="dropped"):
        pipeline.guard(new_count=40, previous_count=100, min_channels=10, min_ratio=0.5)


def fake_run(monkeypatch, sources, alive):
    async def fake_fetch(_):
        return sources

    async def fake_check(urls, timeout, workers):
        return {u: CheckResult(url=u, ok=u in alive, latency_ms=10.0 if u in alive else None) for u in urls}

    async def fake_guide(_):
        return GUIDE

    monkeypatch.setattr(pipeline, "fetch_sources", fake_fetch)
    monkeypatch.setattr(pipeline, "check_urls", fake_check)
    monkeypatch.setattr(pipeline, "fetch_index", fake_guide)


def test_run_writes_playlist_with_fallback_url_and_health(monkeypatch, tmp_path):
    fake_run(monkeypatch, source_results(ENTRIES_A, ENTRIES_B), alive={"https://b/1", "https://a/k1"})

    count = pipeline.run(CONFIG, tmp_path)

    text = (tmp_path / "playlist.m3u").read_text(encoding="utf-8")
    assert count == 2
    assert [(e.group, e.name, e.url, e.tvg_id, e.logo) for e in m3u.parse(text)] == [
        ("Основные", "Первый", "https://b/1", "pervy", "https://logo/pervy.png"),
        ("Кинозалы", "Кинозал 1", "https://a/k1", "", ""),
    ]
    assert text.startswith('#EXTM3U url-tvg="https://epg/x.xml"')
    assert load_health(tmp_path / "health.json")["https://a/1"] == UrlHealth(history="0")


def test_run_keeps_previous_playlist_when_guard_fails(monkeypatch, tmp_path):
    previous = m3u.render([m3u.Entry(name=f"c{i}", url=f"https://old/{i}") for i in range(10)])
    (tmp_path / "playlist.m3u").write_text(previous, encoding="utf-8")
    fake_run(monkeypatch, source_results(ENTRIES_A, ENTRIES_B), alive={"https://a/1"})

    with pytest.raises(pipeline.BuildError, match="dropped"):
        pipeline.run(CONFIG, tmp_path)

    assert (tmp_path / "playlist.m3u").read_text(encoding="utf-8") == previous
    assert not (tmp_path / "health.json").exists()
    assert pipeline.run(CONFIG, tmp_path, force=True) == 1


def test_run_fails_when_all_sources_fail(monkeypatch, tmp_path):
    fake_run(monkeypatch, [SourceResult(source=CONFIG.sources[0], entries=(), error="boom")], alive=set())

    with pytest.raises(pipeline.BuildError, match="All sources failed"):
        pipeline.run(CONFIG, tmp_path)


def test_run_drops_stub_screen_shared_by_several_channels(monkeypatch, tmp_path):
    entries = tuple(m3u.Entry(name=f"Канал {i}", url=f"https://c/{i}", group="Основные") for i in range(4))
    fake_run(monkeypatch, source_results(entries, ()), alive={e.url for e in entries})

    async def fake_check(urls, timeout, workers):
        fingerprint = {"https://c/0": "real"}
        return {u: CheckResult(url=u, ok=True, latency_ms=10.0, fingerprint=fingerprint.get(u, "offline")) for u in urls}

    monkeypatch.setattr(pipeline, "check_urls", fake_check)

    assert pipeline.run(CONFIG, tmp_path) == 1
    assert [e.name for e in m3u.parse((tmp_path / "playlist.m3u").read_text(encoding="utf-8"))] == ["Канал 0"]
