"""Параметры модели: читаются из config.toml, отсутствующий ключ — значение по умолчанию."""
from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


@dataclass
class Config:
    min_markup_pct: float = 25
    default_cover_days: int = 60
    review_period_days: int = 30
    service_level: dict = field(default_factory=lambda: {"A": 0.95, "B": 0.90, "C": 0.80})
    abc_thresholds: tuple = (0.80, 0.95)
    lead_default_days: int = 123
    lead_min_days: int = 30
    lead_max_days: int = 180
    excess_cover_months: int = 12
    overdue_grace_days: int = 90
    overdue_eta_days: int = 30
    reliability_min_ratio: float = 0.95
    reliability_max_uplift: float = 1.2
    reliability_min_age_days: int = 180
    reliability_receipt_window_days: int = 180
    trend_clip: tuple = (0.5, 1.5)
    new_sku_months: int = 6
    partial_day: int = 13
    shrink_min_months: int = 24
    season_own_weight: float = 0.5
    season_outlier_factor: float = 2.0
    level_min_season_index: float = 0.5
    forecast_cap_vs_last_year: float = 2.0
    forecast_months: int = 18
    lot_rounding: tuple = ((100, 10), (1000, 50), (float("inf"), 100))
    dead_months: int = 12
    change_threshold_pct: float = 20
    as_of: date | None = None
    llm_model: str = "deepseek-chat"
    llm_base_url: str = "https://api.deepseek.com"
    llm_timeout_s: int = 60
    site_url: str = ""


_TUPLE_FIELDS = {"abc_thresholds", "trend_clip"}


def _parse_as_of(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"config.toml: as_of должен быть датой ГГГГ-ММ-ДД, а не «{value}»") from exc


def load_config(path: str | Path = "config.toml") -> Config:
    """Читает config.toml; ключа нет в файле (или нет файла) — берётся значение по умолчанию.

    Неизвестные ключи игнорируются: файл могут дополнять другие части системы.
    """
    p = Path(path)
    raw = tomllib.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    known = {f.name for f in dataclasses.fields(Config)}
    values = {}
    for key, value in raw.items():
        if key not in known:
            continue
        if key in _TUPLE_FIELDS:
            value = tuple(value)
        elif key == "lot_rounding":
            value = tuple((float(limit), int(step)) for limit, step in value)
        elif key == "service_level":
            value = {str(k): float(v) for k, v in value.items()}
        elif key == "as_of":
            value = _parse_as_of(value)
        values[key] = value
    return Config(**values)
