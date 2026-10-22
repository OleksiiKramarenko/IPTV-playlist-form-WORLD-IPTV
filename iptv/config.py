"""Loading and validation of config.json."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when config.json is missing or malformed."""


@dataclass(frozen=True)
class Source:
    name: str
    url: str


@dataclass(frozen=True)
class GroupRule:
    name: str
    match: tuple[str, ...]


@dataclass(frozen=True)
class CheckSettings:
    timeout: float = 8.0
    workers: int = 60
    history_size: int = 10
    grace_fails: int = 2


@dataclass(frozen=True)
class OutputSettings:
    file: str = "playlist.m3u"
    epg_urls: tuple[str, ...] = ()
    min_channels: int = 100
    min_ratio: float = 0.5


@dataclass(frozen=True)
class Config:
    sources: tuple[Source, ...]
    groups: tuple[GroupRule, ...]
    exclude_groups: tuple[str, ...]
    skip_vod_files: bool
    check: CheckSettings
    output: OutputSettings
    health_file: str


def load_config(path: Path) -> Config:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ConfigError(f"Config file not found: {path}") from e
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in {path}: {e}") from e
    return parse_config(raw)


def parse_config(raw: Any) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be an object")

    sources = tuple(_parse_source(s) for s in _require_list(raw, "sources"))
    if not sources:
        raise ConfigError("'sources' must contain at least one source")

    groups = tuple(_parse_group(g) for g in _require_list(raw, "groups"))
    if not groups:
        raise ConfigError("'groups' must contain at least one rule")

    return Config(
        sources=sources,
        groups=groups,
        exclude_groups=tuple(_lower_strings(raw.get("exclude_groups", []), "exclude_groups")),
        skip_vod_files=bool(raw.get("skip_vod_files", True)),
        check=_parse_check(raw.get("check", {})),
        output=_parse_output(raw.get("output", {})),
        health_file=str(raw.get("health_file", "health.json")),
    )


def _require_list(raw: dict, key: str) -> list:
    value = raw.get(key)
    if not isinstance(value, list):
        raise ConfigError(f"'{key}' must be a list")
    return value


def _lower_strings(values: Any, field: str) -> list[str]:
    if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
        raise ConfigError(f"'{field}' must be a list of non-empty strings")
    return [v.strip().lower() for v in values]


def _parse_source(raw: Any) -> Source:
    if not isinstance(raw, dict):
        raise ConfigError("Each source must be an object with 'name' and 'url'")
    url = str(raw.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        raise ConfigError(f"Source url must be http(s): {url!r}")
    return Source(name=str(raw.get("name") or url), url=url)


def _parse_group(raw: Any) -> GroupRule:
    if not isinstance(raw, dict) or not str(raw.get("name", "")).strip():
        raise ConfigError("Each group rule must be an object with 'name' and 'match'")
    name = str(raw["name"]).strip()
    match = _lower_strings(raw.get("match"), f"groups[{name}].match")
    if not match:
        raise ConfigError(f"Group rule '{name}' needs at least one match pattern")
    return GroupRule(name=name, match=tuple(match))


def _parse_check(raw: Any) -> CheckSettings:
    if not isinstance(raw, dict):
        raise ConfigError("'check' must be an object")
    settings = CheckSettings(
        timeout=float(raw.get("timeout", CheckSettings.timeout)),
        workers=int(raw.get("workers", CheckSettings.workers)),
        history_size=int(raw.get("history_size", CheckSettings.history_size)),
        grace_fails=int(raw.get("grace_fails", CheckSettings.grace_fails)),
    )
    if settings.timeout <= 0 or settings.workers <= 0 or settings.history_size <= 0:
        raise ConfigError("'check.timeout', 'check.workers' and 'check.history_size' must be positive")
    if settings.grace_fails < 0:
        raise ConfigError("'check.grace_fails' must be >= 0")
    return settings


def _parse_output(raw: Any) -> OutputSettings:
    if not isinstance(raw, dict):
        raise ConfigError("'output' must be an object")
    epg_urls = raw.get("epg_urls", [])
    if not isinstance(epg_urls, list) or not all(isinstance(u, str) for u in epg_urls):
        raise ConfigError("'output.epg_urls' must be a list of strings")
    settings = OutputSettings(
        file=str(raw.get("file", OutputSettings.file)),
        epg_urls=tuple(u.strip() for u in epg_urls if u.strip()),
        min_channels=int(raw.get("min_channels", OutputSettings.min_channels)),
        min_ratio=float(raw.get("min_ratio", OutputSettings.min_ratio)),
    )
    if not 0 <= settings.min_ratio <= 1:
        raise ConfigError("'output.min_ratio' must be between 0 and 1")
    return settings
