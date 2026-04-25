"""
AC controller — bang-bang FSM with debounce, anti-short-cycle, staleness watchdog.

State machine:
  committed_mode  — the mode that has been sent to HA (off / heat / cool)
  pending_mode    — candidate mode waiting out the debounce window
  pending_since   — when the pending mode was first detected

Decision priority (highest overrides lower):
  1. HA WebSocket disconnected  → no new commands (leave AC as-is)
  2. bypass input_boolean OFF   → force OFF
  3. Outside schedule window    → force OFF
  4. Sensor stale               → force OFF + alarm
  5. Manual override (non-auto) → commit override
  6. FSM decision               → debounce → commit

Commit guard:
  A new commit is only sent when min_cycle_seconds has elapsed since the
  last commit (prevents AC short-cycling).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from .config import get_config
from .db import log_event
from .ha_client import get_ha_client
from .models import ControllerState, Mode, Override
from .schedule import in_schedule

log = logging.getLogger(__name__)


class Controller:
    def __init__(self) -> None:
        self.state = ControllerState()
        self._tick_task: Optional[asyncio.Task] = None
        # Listeners notified on every state change (the WS push layer)
        self._listeners: list[asyncio.Queue] = []

    # ------------------------------------------------------------------
    # Public API used by routes
    # ------------------------------------------------------------------

    def add_listener(self, q: asyncio.Queue) -> None:
        self._listeners.append(q)

    def remove_listener(self, q: asyncio.Queue) -> None:
        try:
            self._listeners.remove(q)
        except ValueError:
            pass

    def set_override(self, override: Override) -> None:
        self.state.override = override
        log.info("Manual override set to %s", override)
        # Trigger immediate decision (non-blocking)
        asyncio.create_task(self._decide())

    def update_setpoints(self, heat_below: float, cool_above: float) -> None:
        cfg = get_config()
        cfg.control.heat_below = heat_below
        cfg.control.cool_above = cool_above
        self.state.heat_below = heat_below
        self.state.cool_above = cool_above
        asyncio.create_task(self._decide())

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        ha = get_ha_client()
        ha.on_state_change(self._on_ha_state)
        self._tick_task = asyncio.create_task(self._tick_loop())

    async def stop(self) -> None:
        if self._tick_task:
            self._tick_task.cancel()

    # ------------------------------------------------------------------
    # HA state callback
    # ------------------------------------------------------------------

    async def _on_ha_state(self, entity_id: str, state: str, attributes: dict) -> None:
        cfg = get_config()
        entities = cfg.entities
        ha = get_ha_client()

        if entity_id == "_connection":
            self.state.ha_connected = state == "connected"
            await self._publish()
            return

        self.state.ha_connected = ha.connected

        if entity_id == entities.room_temp:
            try:
                self.state.room_temp = float(state)
                self.state.sensor_stale = False
                self._last_temp_update = time.time()  # track freshness here
            except (ValueError, TypeError):
                log.warning("Bad room_temp value: %r", state)

        elif entity_id == entities.outdoor_temp:
            try:
                self.state.outdoor_temp = float(state)
            except (ValueError, TypeError):
                log.warning("Bad outdoor_temp value: %r", state)

        elif entity_id == entities.bypass:
            # bypass ON  = automation active (pass-through)
            # bypass OFF = user has disabled automation
            self.state.bypass_on = state.lower() == "on"

        # Always re-decide on any relevant entity change
        await self._decide()

    # ------------------------------------------------------------------
    # Tick loop — periodic FSM evaluation
    # ------------------------------------------------------------------

    async def _tick_loop(self) -> None:
        cfg = get_config()
        while True:
            await asyncio.sleep(cfg.app.tick_seconds)
            await self._decide()

    # ------------------------------------------------------------------
    # Core FSM
    # ------------------------------------------------------------------

    async def _decide(self) -> None:
        cfg = get_config()
        ctrl = cfg.control
        now = time.time()

        # ── 1. Schedule ────────────────────────────────────────────────
        if cfg.schedule.enabled:
            self.state.in_schedule = in_schedule(cfg.schedule.start, cfg.schedule.end)
        else:
            self.state.in_schedule = True

        # ── 2. Staleness check ─────────────────────────────────────────
        # _last_temp_update is set in _on_ha_state when the room_temp entity
        # fires. If no update arrives within stale_sensor_seconds, force OFF.
        if not hasattr(self, "_last_temp_update"):
            self._last_temp_update: float = now  # grace period on first start

        stale = (now - self._last_temp_update) > ctrl.stale_sensor_seconds
        self.state.sensor_stale = stale

        # ── Decide target ──────────────────────────────────────────────
        target = self._compute_target(now)

        # ── Apply to pending/committed FSM ─────────────────────────────
        await self._fsm_step(target, now)
        await self._publish()

    def _compute_target(self, now: float) -> Mode:
        """
        Pure target computation — returns desired mode given current readings.
        Does NOT consider debounce or min-cycle (those are in _fsm_step).
        """
        cfg = get_config()
        ctrl = cfg.control

        # Priority 1: disconnected — no change command, return current committed
        if not self.state.ha_connected:
            self.state.reason = "HA disconnected — holding"
            return self.state.committed_mode

        # Priority 2: bypass off
        if not self.state.bypass_on:
            self.state.reason = "bypass off"
            return Mode.OFF

        # Priority 3: outside schedule
        if not self.state.in_schedule:
            self.state.reason = "outside schedule"
            return Mode.OFF

        # Priority 4: stale sensor
        if self.state.sensor_stale:
            self.state.reason = "sensor stale — safety OFF"
            return Mode.OFF

        # Priority 5: manual override
        if self.state.override != Override.AUTO:
            self.state.reason = f"manual override: {self.state.override}"
            return Mode(self.state.override.value)

        # Priority 6: FSM decision (no reading yet → off)
        temp = self.state.room_temp
        if temp is None:
            self.state.reason = "no temp reading yet"
            return Mode.OFF

        current = self.state.committed_mode
        h = ctrl.hysteresis

        # Cooling leg
        if temp > ctrl.cool_above:
            desired = Mode.COOL
        # Heating leg
        elif temp < ctrl.heat_below:
            desired = Mode.HEAT
        # Hysteresis dead-band — hold current mode if within band
        elif current == Mode.COOL and temp > (ctrl.cool_above - h):
            desired = Mode.COOL
        elif current == Mode.HEAT and temp < (ctrl.heat_below + h):
            desired = Mode.HEAT
        else:
            desired = Mode.OFF

        # Outdoor gate: block cooling if outside too cold
        if desired == Mode.COOL and cfg.cooling_outdoor_gate.enabled:
            outdoor = self.state.outdoor_temp
            if outdoor is not None and outdoor <= cfg.cooling_outdoor_gate.min_outdoor_c:
                self.state.reason = (
                    f"cool blocked: outdoor {outdoor:.1f}°C "
                    f"≤ {cfg.cooling_outdoor_gate.min_outdoor_c}°C"
                )
                desired = Mode.OFF

        if desired == Mode.COOL:
            self.state.reason = f"room {self.state.room_temp:.1f}°C > {ctrl.cool_above}°C"
        elif desired == Mode.HEAT:
            self.state.reason = f"room {self.state.room_temp:.1f}°C < {ctrl.heat_below}°C"
        elif desired == Mode.OFF and self.state.reason.startswith("cool blocked"):
            pass  # keep the outdoor-gate reason
        else:
            self.state.reason = f"room {temp:.1f}°C — comfortable"

        return desired

    async def _fsm_step(self, target: Mode, now: float) -> None:
        cfg = get_config()
        ctrl = cfg.control

        committed = self.state.committed_mode

        if target == committed:
            # Stable — clear any pending transition
            self.state.pending_mode = None
            self.state.pending_since = None
            return

        if target == self.state.pending_mode:
            # Already counting down debounce for this target
            elapsed = now - (self.state.pending_since or now)
            time_since_commit = now - (self.state.last_committed_at or 0)

            if elapsed >= ctrl.debounce_seconds and time_since_commit >= ctrl.min_cycle_seconds:
                await self._commit(target)
            # else: still waiting — do nothing

        else:
            # New candidate — start debounce timer
            self.state.pending_mode = target
            self.state.pending_since = now
            log.info(
                "Pending mode → %s (debounce %ss, min_cycle %ss)",
                target,
                ctrl.debounce_seconds,
                ctrl.min_cycle_seconds,
            )

    async def _commit(self, mode: Mode) -> None:
        cfg = get_config()
        ha = get_ha_client()
        entities = cfg.entities
        ctrl = cfg.control

        log.info("Committing mode: %s (was %s)", mode, self.state.committed_mode)

        if mode == Mode.OFF:
            await ha.call_service("switch", "turn_off", entities.ac_power)
        elif mode == Mode.HEAT:
            await ha.call_service("switch", "turn_on", entities.ac_power)
            await asyncio.sleep(1)  # brief pause for power-on settle
            await ha.call_service(
                "climate", "set_temperature", entities.ac_climate,
                {"temperature": ctrl.heat_target_c}
            )
        elif mode == Mode.COOL:
            await ha.call_service("switch", "turn_on", entities.ac_power)
            await asyncio.sleep(1)
            await ha.call_service(
                "climate", "set_temperature", entities.ac_climate,
                {"temperature": ctrl.cool_target_c}
            )

        self.state.committed_mode = mode
        self.state.pending_mode = None
        self.state.pending_since = None
        self.state.last_committed_at = time.time()

        await log_event(
            action=f"commit:{mode.value}",
            reason=self.state.reason,
            committed_mode=mode.value,
            room_temp=self.state.room_temp,
            outdoor_temp=self.state.outdoor_temp,
        )

    # ------------------------------------------------------------------
    # WebSocket push
    # ------------------------------------------------------------------

    async def _publish(self) -> None:
        """Push current state snapshot to all connected WebSocket clients."""
        cfg = get_config()
        self.state.heat_below = cfg.control.heat_below
        self.state.cool_above = cfg.control.cool_above
        data = self.state.model_dump_json()
        dead = []
        for q in self._listeners:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.remove_listener(q)


# Singleton
_controller: Optional[Controller] = None


def get_controller() -> Controller:
    global _controller
    if _controller is None:
        _controller = Controller()
    return _controller
