from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class NightFallConfig:
    repo_root: Path
    ombre_home: Path | None
    data_dir: Path
    min_surface_age_hours: float = 3.0
    surface_threshold: float = 0.62
    attempt_threshold: float = 0.45
    alpha_subordinate: float = 0.25
    spontaneous_surface_prob: float = 0.02  # legacy, unused after morning-window redesign
    selection_limit: int = 5
    # Morning-window surface mechanism:
    morning_surface_prob: float = 0.5  # P(any dream surfaces on first breath in new morning window)
    morning_window_hour: int = 4  # local-time hour that opens a new window
    morning_window_tz_offset: int = 8  # UTC offset for the local clock above

    @property
    def dreams_dir(self) -> Path:
        return self.data_dir / "dreams"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_config_path() -> Path:
    return repo_root() / ".nightfall.yaml"


def load_raw_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or Path(os.environ.get("NIGHT_FALL_CONFIG", default_config_path()))
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Night Fall config must be a YAML mapping: {config_path}")
    return data


def save_local_config(ombre_home: Path, path: Path | None = None) -> Path:
    config_path = path or default_config_path()
    payload = {"ombre_home": str(ombre_home.resolve())}
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return config_path


def _float_env(name: str, fallback: float) -> float:
    value = os.environ.get(name)
    if value in (None, ""):
        return fallback
    try:
        return float(value)
    except ValueError:
        return fallback


def _int_env(name: str, fallback: int) -> int:
    value = os.environ.get(name)
    if value in (None, ""):
        return fallback
    try:
        return int(value)
    except ValueError:
        return fallback


def _resolve_ombre_home(raw: dict[str, Any]) -> Path | None:
    candidates: list[str | Path | None] = [
        os.environ.get("OMBRE_HOME"),
        raw.get("ombre_home"),
    ]
    for candidate in candidates:
        if candidate:
            path = Path(candidate).expanduser().resolve()
            if (path / "server.py").exists():
                return path
            raise FileNotFoundError(f"OMBRE_HOME does not contain server.py: {path}")
    return None


def _resolve_data_dir(raw: dict[str, Any]) -> Path:
    if os.environ.get("NIGHT_FALL_DATA_DIR"):
        return Path(os.environ["NIGHT_FALL_DATA_DIR"]).expanduser().resolve()
    if raw.get("data_dir"):
        return Path(raw["data_dir"]).expanduser().resolve()
    if os.environ.get("OMBRE_BUCKETS_DIR"):
        return (Path(os.environ["OMBRE_BUCKETS_DIR"]).expanduser().resolve() / "night_fall")
    return repo_root() / "data"


def load_config(require_ombre: bool = True) -> NightFallConfig:
    raw = load_raw_config()
    ombre_home = _resolve_ombre_home(raw)
    if require_ombre and ombre_home is None:
        raise FileNotFoundError(
            "Night Fall needs an existing Ombre install. Set OMBRE_HOME or run "
            "python scripts/install_local.py."
        )

    legacy_spontaneous = float(raw.get("spontaneous_chance", 0.02))
    spontaneous_default = float(raw.get("spontaneous_surface_prob", legacy_spontaneous))

    cfg = NightFallConfig(
        repo_root=repo_root(),
        ombre_home=ombre_home,
        data_dir=_resolve_data_dir(raw),
        min_surface_age_hours=_float_env(
            "NIGHT_FALL_MIN_SURFACE_AGE_HOURS",
            float(raw.get("min_surface_age_hours", 3.0)),
        ),
        surface_threshold=_float_env(
            "NIGHT_FALL_SURFACE_THRESHOLD",
            float(raw.get("surface_threshold", 0.62)),
        ),
        attempt_threshold=_float_env(
            "NIGHT_FALL_ATTEMPT_THRESHOLD",
            float(raw.get("attempt_threshold", 0.45)),
        ),
        alpha_subordinate=_float_env(
            "NIGHT_FALL_ALPHA_SUBORDINATE",
            float(raw.get("alpha_subordinate", 0.25)),
        ),
        spontaneous_surface_prob=_float_env(
            "NIGHT_FALL_SPONTANEOUS_SURFACE_PROB",
            _float_env("NIGHT_FALL_SPONTANEOUS_CHANCE", spontaneous_default),
        ),
        selection_limit=_int_env("NIGHT_FALL_SELECTION_LIMIT", int(raw.get("selection_limit", 5))),
        morning_surface_prob=_float_env(
            "NIGHT_FALL_MORNING_SURFACE_PROB",
            float(raw.get("morning_surface_prob", 0.5)),
        ),
        morning_window_hour=_int_env(
            "NIGHT_FALL_MORNING_WINDOW_HOUR",
            int(raw.get("morning_window_hour", 4)),
        ),
        morning_window_tz_offset=_int_env(
            "NIGHT_FALL_MORNING_WINDOW_TZ_OFFSET",
            int(raw.get("morning_window_tz_offset", 8)),
        ),
    )
    if cfg.attempt_threshold >= cfg.surface_threshold:
        raise ValueError(
            f"attempt_threshold ({cfg.attempt_threshold}) must be strictly less than "
            f"surface_threshold ({cfg.surface_threshold})."
        )
    cfg.dreams_dir.mkdir(parents=True, exist_ok=True)
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    return cfg
