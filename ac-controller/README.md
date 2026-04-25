# ac-controller

A small custom controller that replaces a Node-RED AC cool/heat flow. Runs as a
Docker container on Unraid (or anywhere), talks to an existing Home Assistant
instance over the WebSocket API, and provides a custom web GUI for monitoring
and adjusting the control loop.

The physical AC is switched fully on/off via an HA `switch` entity, and when
on, its setpoint is slammed to `cool_target_c` (default 18°C) or `heat_target_c`
(default 31°C) so the AC always runs at full bore until the room reaches
target.

## Features

- **Bang-bang control** with hysteresis + debounce + anti-short-cycle
- **Real-time GUI** over WebSocket, no page reloads
- **Manual override** buttons: Auto / Off / Force Heat / Force Cool
- **Bypass integration** — mirrors `input_boolean.ac_bypass` in HA
- **Schedule** — only runs inside the configured time window
- **Outdoor-temp gate** — refuses to cool if outside is too cold
- **Stale sensor watchdog** — forces AC off if the room-temp probe goes silent
- **Event log** for diagnostics (last N events, kept in SQLite)
- **Persistent config** in SQLite — edit everything from the GUI
- **No history/graphing** — keeps the app tiny and focused

## Architecture

```
┌───────────────────── Unraid ─────────────────────┐
│                                                  │
│  VM: Home Assistant (192.168.1.3)                │
│      ├─ sensor.0xa4c138cfaad13a80_temperature    │
│      ├─ sensor.gw1100a_outdoor_temperature       │
│      ├─ switch.air_conditioner_switch            │
│      ├─ climate.air_conditioner                  │
│      └─ input_boolean.ac_bypass                  │
│            ▲                                     │
│            │ HA WebSocket API                    │
│            ▼                                     │
│  Docker: ac-controller (this project)            │
│      FastAPI + HTMX GUI on :8765                 │
│      SQLite state in /mnt/user/appdata/…         │
└──────────────────────────────────────────────────┘
```

## Quick start (local dev)

```bash
git clone <this-repo> ac-controller
cd ac-controller
cp .env.example .env
# edit .env, put your Home Assistant long-lived access token in HA_TOKEN
docker compose up --build
# open http://localhost:8765
```

## Quick start (Unraid, using the pre-built image from GHCR)

1. SSH to Unraid or use the terminal plugin.
2. Create the appdata directory:
   ```bash
   mkdir -p /mnt/user/appdata/ac-controller
   ```
3. Drop a `docker-compose.yml` somewhere (e.g. `/mnt/user/appdata/ac-controller/`):
   ```yaml
   services:
     ac-controller:
       image: ghcr.io/OWNER/ac-controller:latest   # replace OWNER
       container_name: ac-controller
       restart: unless-stopped
       environment:
         HA_URL: http://192.168.1.3:8123
         HA_TOKEN: ${HA_TOKEN}
         TZ: Europe/London
       volumes:
         - /mnt/user/appdata/ac-controller:/data
       ports:
         - "8765:8000"
   ```
4. Create a `.env` next to it with:
   ```
   HA_TOKEN=eyJ0eXAiOiJKV1QiLC...    (your long-lived token)
   ```
5. `docker compose up -d`
6. Visit `http://<unraid-ip>:8765`

## Getting a Home Assistant long-lived access token

1. In HA, click your user avatar (bottom-left) → scroll to "Long-Lived Access Tokens"
2. Click "Create Token", name it e.g. `ac-controller`
3. Copy the token immediately (you can't see it again) and put it in `.env`

The token needs access to the entities listed above. A normal admin user's token is sufficient.

## Configuration

Everything is editable from the GUI's Settings page and persisted in SQLite.

First-run defaults come from `config.yaml` (mounted into the container, or the
bundled `config.example.yaml` is used if none is provided).

Key settings:

| Setting | Default | Notes |
|---|---|---|
| `heat_below` | 21.0 | Heat if room temp drops below this |
| `cool_above` | 23.0 | Cool if room temp rises above this |
| `hysteresis` | 1.0 | Deadband inside the 21–23 window |
| `debounce_seconds` | 300 | Target must persist this long before commit |
| `min_cycle_seconds` | 600 | Minimum time between commits (anti-short-cycle) |
| `stale_sensor_seconds` | 600 | No reading in this long → force OFF |
| `heat_target_c` | 31 | AC setpoint when heating (full-blast) |
| `cool_target_c` | 18 | AC setpoint when cooling (full-blast) |
| `schedule.start` / `.end` | 08:00 / 23:00 | Outside this window → force OFF |
| `cooling_outdoor_gate.min_outdoor_c` | 17 | Don't cool if outside < this |

## Behaviour

The controller's decision logic:

```
if bypass == off              → force OFF
elif outside schedule         → force OFF
elif sensor is stale          → force OFF + alarm
elif manual override set      → commit override
else:
    decide target from temp + hysteresis
    if would-cool and outside <= min_outdoor_c → force OFF
    wait debounce_seconds while target is stable
    respect min_cycle_seconds since last commit
    commit: set switch + set climate setpoint
```

Commits are **mutually exclusive** — the controller will never try to heat and
cool at the same time.

On startup the controller does **not** change the AC state; it waits for the
first sensor reading and then decides. On clean shutdown it also leaves the AC
untouched.

## Building and publishing

This repo ships a GitHub Actions workflow that builds multi-arch images
(`linux/amd64` + `linux/arm64`) and pushes them to GitHub Container Registry.

- Pushes to `main` → `ghcr.io/OWNER/ac-controller:latest`
- Tags `v1.2.3` → `ghcr.io/OWNER/ac-controller:1.2.3`, `:1.2`, `:1`
- Every commit → `ghcr.io/OWNER/ac-controller:sha-<short>`
- Images are signed keyless with cosign (verify: `cosign verify ghcr.io/…`)

To cut a release:

```bash
git tag v0.1.0
git push --tags
```

## Development

```bash
make dev       # run locally with hot reload
make lint      # run ruff
make build     # build the docker image
make run       # run the container
```

Requires Python 3.12+.

## License

MIT. See `LICENSE`.