"""Shared data models and enums."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class Mode(str, Enum):
    OFF = "off"
    HEAT = "heat"
    COOL = "cool"
    AUTO = "auto"  # controller decides


class Override(str, Enum):
    AUTO = "auto"
    OFF = "off"
    HEAT = "heat"
    COOL = "cool"


class ControllerState(BaseModel):
    """Live snapshot emitted to the GUI via WebSocket."""

    # Temperatures
    room_temp: Optional[float] = None
    outdoor_temp: Optional[float] = None

    # Controller state
    committed_mode: Mode = Mode.OFF
    pending_mode: Optional[Mode] = None
    pending_since: Optional[float] = None  # epoch seconds
    last_committed_at: Optional[float] = None  # epoch seconds

    # Flags
    bypass_on: bool = False  # True = bypass active (automation holds); False = automation runs
    in_schedule: bool = True
    sensor_stale: bool = False
    ha_connected: bool = False
    override: Override = Override.AUTO

    # Human-readable reason for current state
    reason: str = "starting"

    # Setpoints (mirrored from settings for the GUI)
    heat_below: float = 21.0
    cool_above: float = 23.0


class EventEntry(BaseModel):
    id: int
    ts: float  # epoch seconds
    action: str
    reason: str
    room_temp: Optional[float]
    outdoor_temp: Optional[float]
    committed_mode: str
