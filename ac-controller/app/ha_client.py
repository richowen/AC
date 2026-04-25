"""
Async Home Assistant WebSocket client.

Subscribes to state_changed events for the entities we care about,
and provides a call_service helper for actuation.

Reconnects automatically with exponential back-off on disconnection.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Coroutine, Optional

import aiohttp

from .config import get_config

log = logging.getLogger(__name__)

# Callback type: async def on_state(entity_id, new_state, new_attributes)
StateCallback = Callable[[str, str, dict], Coroutine]


class HAClient:
    def __init__(self) -> None:
        self._cfg = get_config()
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._id = 1
        self._connected = False
        self._callbacks: list[StateCallback] = []
        self._task: Optional[asyncio.Task] = None
        # Latest known states {entity_id: {"state": str, "attributes": dict}}
        self._states: dict[str, dict] = {}

    def on_state_change(self, cb: StateCallback) -> None:
        """Register a coroutine callback for entity state changes."""
        self._callbacks.append(cb)

    @property
    def connected(self) -> bool:
        return self._connected

    def get_state(self, entity_id: str) -> Optional[str]:
        return self._states.get(entity_id, {}).get("state")

    def get_attributes(self, entity_id: str) -> dict:
        return self._states.get(entity_id, {}).get("attributes", {})

    # ------------------------------------------------------------------
    # Public start / stop
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Actuation
    # ------------------------------------------------------------------

    async def call_service(
        self, domain: str, service: str, entity_id: str, data: Optional[dict] = None
    ) -> None:
        """Call a HA service. Fire-and-forget (does not wait for result)."""
        if not self._connected or self._ws is None:
            log.warning("call_service: not connected, skipping %s.%s", domain, service)
            return
        payload: dict[str, Any] = {
            "id": self._next_id(),
            "type": "call_service",
            "domain": domain,
            "service": service,
            "target": {"entity_id": entity_id},
        }
        if data:
            payload["service_data"] = data
        try:
            await self._ws.send_json(payload)
            log.info("call_service: %s.%s → %s %s", domain, service, entity_id, data or "")
        except Exception as exc:
            log.error("call_service failed: %s", exc)

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _run_forever(self) -> None:
        backoff = 2
        while True:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.error("HA WS error: %s — reconnecting in %ss", exc, backoff)
            self._connected = False
            await self._notify_all("_connection", "disconnected", {})
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _connect_and_listen(self) -> None:
        cfg = self._cfg
        ws_url = cfg.ha_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_url}/api/websocket"

        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

        log.info("Connecting to HA WebSocket at %s", ws_url)
        async with self._session.ws_connect(ws_url, heartbeat=30) as ws:
            self._ws = ws

            # 1. Auth handshake
            msg = await ws.receive_json()
            assert msg["type"] == "auth_required", f"Unexpected: {msg}"
            await ws.send_json({"type": "auth", "access_token": cfg.ha_token})
            msg = await ws.receive_json()
            if msg["type"] == "auth_invalid":
                raise ValueError("HA authentication failed — check HA_TOKEN")
            assert msg["type"] == "auth_ok", f"Unexpected: {msg}"
            log.info("HA WebSocket authenticated")

            # 2. Fetch current states for all our entities
            await self._fetch_states(ws)

            # 3. Subscribe to state_changed events
            sub_id = self._next_id()
            await ws.send_json(
                {"id": sub_id, "type": "subscribe_events", "event_type": "state_changed"}
            )
            msg = await ws.receive_json()
            assert msg.get("success"), f"Subscribe failed: {msg}"

            self._connected = True
            log.info("HA WebSocket subscribed to state_changed")

            # 4. Dispatch initial states so controller can bootstrap
            for eid, state_data in self._states.items():
                await self._notify_all(eid, state_data["state"], state_data["attributes"])

            # 5. Listen loop
            async for raw in ws:
                if raw.type == aiohttp.WSMsgType.TEXT:
                    msg = json.loads(raw.data)
                    if msg.get("type") == "event":
                        ed = msg["event"].get("data", {})
                        new = ed.get("new_state") or {}
                        entity_id = ed.get("entity_id", "")
                        if new and entity_id:
                            state_str = new.get("state", "")
                            attrs = new.get("attributes", {})
                            self._states[entity_id] = {"state": state_str, "attributes": attrs}
                            await self._notify_all(entity_id, state_str, attrs)
                elif raw.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break

    async def _fetch_states(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Fetch current states for all watched entities via get_states."""
        req_id = self._next_id()
        await ws.send_json({"id": req_id, "type": "get_states"})
        cfg = get_config()
        watched = {
            cfg.entities.room_temp,
            cfg.entities.outdoor_temp,
            cfg.entities.ac_power,
            cfg.entities.ac_climate,
            cfg.entities.bypass,
        }
        while True:
            msg = await ws.receive_json()
            if msg.get("id") == req_id:
                for entity in msg.get("result", []):
                    eid = entity.get("entity_id", "")
                    if eid in watched:
                        self._states[eid] = {
                            "state": entity.get("state", ""),
                            "attributes": entity.get("attributes", {}),
                        }
                break

    async def _notify_all(self, entity_id: str, state: str, attributes: dict) -> None:
        for cb in self._callbacks:
            try:
                await cb(entity_id, state, attributes)
            except Exception as exc:
                log.exception("State callback error for %s: %s", entity_id, exc)

    def _next_id(self) -> int:
        self._id += 1
        return self._id


# Singleton
_client: Optional[HAClient] = None


def get_ha_client() -> HAClient:
    global _client
    if _client is None:
        _client = HAClient()
    return _client
