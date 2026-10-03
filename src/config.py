"""Configuration loading (config/banks.yaml and config/settings.yaml)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


@dataclass
class Bank:
    id: str
    name: str
    jobs_url: str = ""
    source_type: str = "unknown"
    enabled: bool = False
    display_name: str = ""
    country: str = "Germany"
    germany_only: bool = False      # source page already guarantees Germany-only vacancies
    germany_filter: str = ""        # how Germany is filtered at the source (e.g. "url", "api_param")
    location_filter: list = field(default_factory=list)
    alias_of: Optional[str] = None  # same employer monitored under another id
    verification_status: str = "unverified"
    last_verified: Optional[str] = None
    notes: str = ""
    options: dict = field(default_factory=dict)  # scraper-specific settings

    @property
    def label(self) -> str:
        return self.display_name or self.name

    @classmethod
    def from_dict(cls, d: dict) -> "Bank":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        if known.get("location_filter") is None:
            known.pop("location_filter", None)
        if known.get("options") is None:
            known.pop("options", None)
        return cls(**known)


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_banks(path: Path | None = None) -> list[Bank]:
    data = load_yaml(path or CONFIG_DIR / "banks.yaml")
    banks = [Bank.from_dict(b) for b in data.get("banks", [])]
    ids = [b.id for b in banks]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"Duplicate bank ids in banks.yaml: {sorted(dupes)}")
    return banks


def load_settings(path: Path | None = None) -> dict[str, Any]:
    return load_yaml(path or CONFIG_DIR / "settings.yaml")


def env(name: str) -> str:
    return os.environ.get(name, "").strip()
