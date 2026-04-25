"""
Configuration loader.

Priority (lowest → highest):
  1. Bundled defaults (hardcoded here)
  2. /data/config.yaml  (volume-mounted by user)
  3. Environment variables  HA_URL, HA_TOKEN, TZ
  4. DB overrides (written by the GUI, loaded at startup)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------

@dataclass
class EntitiesConfig:
    room_temp: str = "sensor.0xa4c138cfaad13a80_temperature"
    outdoor_temp: str = "sensor.gw1100a_outdoor_temperature"
    ac_power: str = "switch.air_conditioner_switch"
    ac_climate: str = "climate.air_conditioner"
    bypass: str = "input_boolean.ac_bypass"


@dataclass
class ControlConfig:
    heat_below: float = 21.0
    cool_above: float = 23.0
    hysteresis: float = 1.0
    debounce_seconds: int = 300
    min_cycle_seconds: int = 600
    stale_sensor_seconds: int = 600
    heat_target_c: int = 31
    cool_target_c: int = 18


@dataclass
class OutdoorGateConfig:
    enabled: bool = True
    min_outdoor_c: float = 17.0


@dataclass
class ScheduleConfig:
    enabled: bool = True
    start: str = "08:00"
    end: str = "23:00"


@dataclass
class AppConfig:
    event_log_limit: int = 500
    tick_seconds: int = 30


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------

@dataclass
class Config:
    ha_url: str = "http://192.168.1.3:8123"
    ha_token: str = ""
    entities: EntitiesConfig = field(default_factory=EntitiesConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    cooling_outdoor_gate: OutdoorGateConfig = field(default_factory=OutdoorGateConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    app: AppConfig = field(default_factory=AppConfig)
    data_dir: Path = field(default_factory=lambda: Path("/data"))


def _merge(base: dict, override: dict) -> dict:
    """Deep-merge two dicts (override wins)."""
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _merge(result[k], v)
        else:
            result[k] = v
    return result


def load_config() -> Config:
    cfg = Config()

    # ---------------------------------------------------------------------------
    # 1. Load /data/config.yaml if it exists (user can also sym-link config.yaml)
    # ---------------------------------------------------------------------------
    data_dir = Path(os.environ.get("DATA_DIR", "/data"))
    cfg.data_dir = data_dir
    yaml_path = data_dir / "config.yaml"
    if not yaml_path.exists():
        # Fall back to project-root config.yaml (dev mode) or example
        for candidate in [Path("config.yaml"), Path("config.example.yaml")]:
            if candidate.exists():
                yaml_path = candidate
                break

    file_data: dict = {}
    if yaml_path.exists():
        with open(yaml_path) as fh:
            file_data = yaml.safe_load(fh) or {}

    # Apply entity overrides from YAML
    if "entities" in file_data:
        e = file_data["entities"]
        for k, v in e.items():
            if hasattr(cfg.entities, k):
                setattr(cfg.entities, k, v)

    if "control" in file_data:
        for k, v in file_data["control"].items():
            if hasattr(cfg.control, k):
                setattr(cfg.control, k, v)

    if "cooling_outdoor_gate" in file_data:
        for k, v in file_data["cooling_outdoor_gate"].items():
            if hasattr(cfg.cooling_outdoor_gate, k):
                setattr(cfg.cooling_outdoor_gate, k, v)

    if "schedule" in file_data:
        for k, v in file_data["schedule"].items():
            if hasattr(cfg.schedule, k):
                setattr(cfg.schedule, k, v)

    if "app" in file_data:
        for k, v in file_data["app"].items():
            if hasattr(cfg.app, k):
                setattr(cfg.app, k, v)

    # ---------------------------------------------------------------------------
    # 2. Environment variables override everything
    # ---------------------------------------------------------------------------
    if ha_url := os.environ.get("HA_URL"):
        cfg.ha_url = ha_url.rstrip("/")
    if ha_token := os.environ.get("HA_TOKEN"):
        cfg.ha_token = ha_token

    return cfg


# Singleton — imported everywhere
_config: Optional[Config] = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config() -> Config:
    """Force re-load (called after GUI settings save)."""
    global _config
    _config = load_config()
    return _config
