import json
from pathlib import Path

import pytest

from iptv.config import ConfigError, load_config, parse_config

MINIMAL = {
    "sources": [{"name": "s", "url": "https://example.com/a.m3u"}],
    "groups": [{"name": "Кино", "match": ["КИНО"]}],
}


def test_repository_config_is_valid():
    config = load_config(Path(__file__).resolve().parent.parent / "config.json")

    assert config.sources
    assert config.groups


def test_defaults_and_lowercased_patterns():
    config = parse_config(MINIMAL)

    assert config.groups[0].match == ("кино",)
    assert config.check.timeout == 8.0
    assert config.check.stub_min_channels == 3
    assert config.output.file == "playlist.m3u"
    assert config.health_file == "health.json"
    assert config.retired_file == "retired.json"
    assert config.check.retire_days == 30
    assert config.skip_vod_files is True
    assert config.exclude_channels == ()


@pytest.mark.parametrize(
    "patch, message",
    [
        ({"sources": []}, "at least one source"),
        ({"sources": [{"url": "ftp://x"}]}, "http"),
        ({"groups": "nope"}, "must be a list"),
        ({"groups": [{"name": "A", "match": []}]}, "at least one match"),
        ({"groups": [{"name": "A", "match": [""]}]}, "non-empty strings"),
        ({"check": {"workers": 0}}, "positive"),
        ({"check": {"grace_fails": -1}}, ">= 0"),
        ({"check": {"stub_min_channels": 1}}, "0 .* or at least 2"),
        ({"check": {"retire_days": -1}}, "retire_days"),
        ({"output": {"min_ratio": 2}}, "between 0 and 1"),
        ({"epg": {"urls": ["ftp://x"]}}, "http"),
        ({"epg": {"aliases": {"Inter": ""}}}, "guide ids"),
        ({"epg": {"skip_groups": "Фильмы"}}, "list of group names"),
        ({"epg": []}, "must be an object"),
        ({"exclude_channels": ["*pоссия*"]}, "mixes Latin and Cyrillic"),
        ({"exclude_groups": ["кинo"]}, "mixes Latin and Cyrillic"),
    ],
)
def test_invalid_configs_are_rejected(patch, message):
    with pytest.raises(ConfigError, match=message):
        parse_config({**MINIMAL, **patch})


def test_load_config_reports_missing_and_broken_files(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.json")
    broken = tmp_path / "config.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(ConfigError, match="Invalid JSON"):
        load_config(broken)


def test_load_config_reads_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(MINIMAL), encoding="utf-8")

    assert load_config(path).sources[0].name == "s"
