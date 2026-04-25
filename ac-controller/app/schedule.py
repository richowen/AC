"""Time-of-day schedule helper."""
from __future__ import annotations

from datetime import datetime, time


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def in_schedule(start: str, end: str) -> bool:
    """Return True if the current local time is within [start, end)."""
    now = datetime.now().time().replace(second=0, microsecond=0)
    t_start = parse_hhmm(start)
    t_end = parse_hhmm(end)
    if t_start <= t_end:
        return t_start <= now < t_end
    # Overnight window (e.g. 22:00 – 06:00)
    return now >= t_start or now < t_end
