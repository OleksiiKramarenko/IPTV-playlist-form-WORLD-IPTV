from datetime import datetime, timezone

from iptv.channels import Channel
from iptv.checker import CheckResult
from iptv.health import UrlHealth, load_health, pick_url, save_health, update_health

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def channel(*urls: str) -> Channel:
    return Channel(key="k", name="K", group="G", logo="", tvg_id="", urls=urls)


def test_url_health_properties():
    h = UrlHealth(history="110100")

    assert h.success_rate == 0.5
    assert h.consecutive_fails == 2
    assert UrlHealth().success_rate == 0.0


def test_update_health_appends_trims_and_prunes():
    previous = {
        "https://a": UrlHealth(history="1111", latency_ms=100.0, last_ok="old"),
        "https://gone": UrlHealth(history="1"),
    }
    results = {
        "https://a": CheckResult(url="https://a", ok=False),
        "https://b": CheckResult(url="https://b", ok=True, latency_ms=50.0),
    }

    updated = update_health(previous, results, history_size=3, now=NOW)

    assert updated["https://a"] == UrlHealth(history="110", latency_ms=100.0, last_ok="old")
    assert updated["https://b"] == UrlHealth(history="1", latency_ms=50.0, last_ok="2026-01-01T00:00:00+00:00")
    assert "https://gone" not in updated
    assert previous["https://a"].history == "1111"


def test_pick_prefers_reliable_alive_url():
    health = {
        "https://flaky": UrlHealth(history="0101", latency_ms=10.0),
        "https://stable": UrlHealth(history="1111", latency_ms=900.0),
        "https://dead": UrlHealth(history="1110"),
    }

    assert pick_url(channel("https://flaky", "https://dead", "https://stable"), health, grace_fails=2) == "https://stable"


def test_pick_uses_latency_as_tiebreaker():
    health = {"https://slow": UrlHealth(history="11", latency_ms=900.0), "https://fast": UrlHealth(history="11", latency_ms=90.0)}

    assert pick_url(channel("https://slow", "https://fast"), health, grace_fails=0) == "https://fast"


def test_pick_keeps_recently_good_url_within_grace():
    health = {"https://a": UrlHealth(history="1110")}

    assert pick_url(channel("https://a"), health, grace_fails=2) == "https://a"
    assert pick_url(channel("https://a"), {"https://a": UrlHealth(history="1000")}, grace_fails=2) is None


def test_pick_drops_never_working_urls():
    assert pick_url(channel("https://a"), {"https://a": UrlHealth(history="0")}, grace_fails=2) is None
    assert pick_url(channel("https://unknown"), {}, grace_fails=2) is None


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "health.json"
    health = {"https://a": UrlHealth(history="10", latency_ms=1.5, last_ok="2026-01-01T00:00:00+00:00")}

    save_health(path, health)

    assert load_health(path) == health


def test_load_tolerates_missing_and_corrupt_files(tmp_path):
    assert load_health(tmp_path / "missing.json") == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_health(broken) == {}
