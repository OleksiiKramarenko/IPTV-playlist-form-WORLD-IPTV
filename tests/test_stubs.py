from iptv.channels import Channel
from iptv.checker import CheckResult
from iptv.stubs import mark_shared_stubs


def channel(key: str, *urls: str) -> Channel:
    return Channel(key=key, name=key, group="G", logo="", tvg_id="", urls=urls)


def alive(url: str, fingerprint: str) -> CheckResult:
    return CheckResult(url=url, ok=True, latency_ms=10.0, fingerprint=fingerprint)


def test_marks_media_shared_by_several_channels_as_stub():
    channels = [channel("a", "https://a"), channel("b", "https://b"), channel("c", "https://c"), channel("d", "https://d")]
    results = {
        "https://a": alive("https://a", "offline"),
        "https://b": alive("https://b", "offline"),
        "https://c": alive("https://c", "offline"),
        "https://d": alive("https://d", "unique"),
    }

    marked = mark_shared_stubs(channels, results, min_channels=3)

    assert [u for u, r in marked.items() if r.stub] == ["https://a", "https://b", "https://c"]
    assert marked["https://a"] == CheckResult(url="https://a", ok=False, fingerprint="offline", stub=True)
    assert marked["https://d"] == results["https://d"]
    assert results["https://a"].ok is True


def test_mirrors_of_one_channel_are_not_a_stub():
    channels = [channel("a", "https://a1", "https://a2", "https://a3"), channel("b", "https://b")]
    results = {u: alive(u, "same") for u in ("https://a1", "https://a2", "https://a3", "https://b")}

    marked = mark_shared_stubs(channels, results, min_channels=3)

    assert not any(r.stub for r in marked.values())


def test_failed_checks_and_disabled_detection_are_left_alone():
    channels = [channel(k, f"https://{k}") for k in "abc"]
    results = {f"https://{k}": CheckResult(url=f"https://{k}", ok=False, fingerprint="x") for k in "abc"}
    shared = {f"https://{k}": alive(f"https://{k}", "x") for k in "abc"}

    assert mark_shared_stubs(channels, results, min_channels=3) == results
    assert mark_shared_stubs(channels, shared, min_channels=0) == shared
