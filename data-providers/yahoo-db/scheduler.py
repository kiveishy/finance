"""Daily update scheduler: runs the stock data update once a day at a configured time and time zone."""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

LOCAL_ZONE = "local"  # the server's own time zone
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_MAX_WAIT_SECONDS = 60.0  # re-check the clock at least this often (sleep/clock changes)


@dataclass(frozen=True)
class ScheduleConfig:
    enabled: bool = True
    run_time: str = "23:30"  # HH:MM, 24h
    timezone: str = "Asia/Jerusalem"
    catch_up: bool = True  # run a missed update (service was down at run time) after start

    @classmethod
    def from_dict(cls, raw: Any, defaults: "ScheduleConfig | None" = None) -> "ScheduleConfig":
        """Validated config; missing keys keep `defaults`. Raises ValueError with a user-facing message."""
        base = defaults or cls()
        if not isinstance(raw, Mapping):
            raise ValueError("Schedule must be a JSON object")
        unknown = set(raw) - {"enabled", "run_time", "timezone", "catch_up"}
        if unknown:
            raise ValueError(f"Unknown schedule field(s): {', '.join(sorted(unknown))}")
        enabled = raw.get("enabled", base.enabled)
        catch_up = raw.get("catch_up", base.catch_up)
        run_time = raw.get("run_time", base.run_time)
        zone = raw.get("timezone", base.timezone)
        if not isinstance(enabled, bool) or not isinstance(catch_up, bool):
            raise ValueError("'enabled' and 'catch_up' must be true or false")
        if not isinstance(run_time, str) or not _TIME_RE.match(run_time):
            raise ValueError("'run_time' must be a time HH:MM (00:00-23:59)")
        if not isinstance(zone, str) or not zone:
            raise ValueError("'timezone' must be a time zone name")
        resolve_zone(zone)
        return cls(enabled=enabled, run_time=run_time, timezone=zone, catch_up=catch_up)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def clock_time(self) -> time:
        hours, minutes = self.run_time.split(":")
        return time(int(hours), int(minutes))


def resolve_zone(name: str) -> Optional[ZoneInfo]:
    """ZoneInfo for an IANA name, or None for the server's local time. Raises ValueError for unknown names."""
    if name == LOCAL_ZONE:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown time zone {name!r}") from exc


def _at(day: date, clock: time, zone: Optional[ZoneInfo]) -> datetime:
    # Naive datetimes are interpreted as server local time by astimezone().
    return datetime.combine(day, clock, tzinfo=zone).astimezone(timezone.utc)


def next_run(config: ScheduleConfig, now: datetime) -> datetime:
    """First run time strictly after `now` (aware UTC)."""
    zone = resolve_zone(config.timezone)
    today = now.astimezone(zone).date() if zone else now.astimezone().date()
    for offset in range(3):
        candidate = _at(today + timedelta(days=offset), config.clock_time, zone)
        if candidate > now:
            return candidate
    raise RuntimeError("no next run found")  # unreachable


def previous_run(config: ScheduleConfig, now: datetime) -> datetime:
    """Latest run time at or before `now` (aware UTC)."""
    zone = resolve_zone(config.timezone)
    today = now.astimezone(zone).date() if zone else now.astimezone().date()
    for offset in range(3):
        candidate = _at(today - timedelta(days=offset), config.clock_time, zone)
        if candidate <= now:
            return candidate
    raise RuntimeError("no previous run found")  # unreachable


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds") if value else None


class DailyScheduler:
    """Background thread that calls `job()` at the configured time; `job` returns a short result text."""

    def __init__(
        self,
        config: ScheduleConfig,
        job: Callable[[], str],
        state_file: Path,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        catch_up_delay: float = 60.0,
    ) -> None:
        self._config = config
        self._job = job
        self._state_file = state_file
        self._clock = clock
        self._catch_up_delay = catch_up_delay
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running_since: Optional[datetime] = None
        self._state = self._load_state()
        self._catch_up_at: Optional[datetime] = None

    # ---- public API ----------------------------------------------------------------------------

    @property
    def config(self) -> ScheduleConfig:
        return self._config

    def start(self) -> None:
        now = self._clock()
        if not self._state.get("scheduled_for"):
            # First start: nothing was missed yet.
            self._record(scheduled_for=previous_run(self._config, now), result="not run yet", started=None)
        self._plan_catch_up(now)
        self._thread = threading.Thread(target=self._loop, name="daily-update-scheduler", daemon=True)
        self._thread.start()
        logger.info("Daily update scheduler started: %s", self.describe())

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def update(self, config: ScheduleConfig) -> None:
        with self._lock:
            self._config = config
            self._catch_up_at = None
        logger.info("Daily update schedule changed: %s", self.describe())
        self._wake.set()

    def describe(self) -> str:
        c = self._config
        if not c.enabled:
            return "disabled"
        return f"every day at {c.run_time} ({c.timezone}), next {_iso(next_run(c, self._clock()))}"

    def status(self) -> dict:
        now = self._clock()
        with self._lock:
            config = self._config
            due = self._catch_up_at
        upcoming = next_run(config, now) if config.enabled else None
        if config.enabled and due and due < upcoming:
            upcoming = due
        return {
            **config.to_dict(),
            "next_run": _iso(upcoming),
            "next_run_is_catch_up": bool(config.enabled and due and upcoming == due),
            "running_since": _iso(self._running_since),
            "last_run": dict(self._state),
            "server_time": _iso(now),
        }

    # ---- internals ------------------------------------------------------------------------------

    def _plan_catch_up(self, now: datetime) -> None:
        config = self._config
        if not (config.enabled and config.catch_up):
            return
        missed = previous_run(config, now)
        last = self._state.get("scheduled_for")
        if last and datetime.fromisoformat(last) < missed and now - missed < timedelta(days=1):
            self._catch_up_at = now + timedelta(seconds=self._catch_up_delay)
            logger.info("The update scheduled for %s was missed; running it at %s", _iso(missed), _iso(self._catch_up_at))

    def _loop(self) -> None:
        last_check = self._clock()
        while not self._stop.is_set():
            now = self._clock()
            with self._lock:
                config = self._config
                catch_up_at = self._catch_up_at
            if not config.enabled:
                last_check = now
                self._wait(_MAX_WAIT_SECONDS)
                continue
            # The first slot after the previous check: still found when the wake-up comes late.
            scheduled = next_run(config, last_check)
            if catch_up_at and catch_up_at < scheduled:
                if now >= catch_up_at:
                    self._run(previous_run(config, now))
                    last_check = self._clock()
                    continue
                target = catch_up_at
            elif now >= scheduled:
                self._run(scheduled)
                last_check = self._clock()
                continue
            else:
                target = scheduled
            last_check = now
            self._wait(min((target - now).total_seconds(), _MAX_WAIT_SECONDS))

    def _wait(self, seconds: float) -> None:
        self._wake.wait(seconds)
        self._wake.clear()

    def _run(self, scheduled_for: datetime) -> None:
        with self._lock:
            self._catch_up_at = None
        started = self._clock()
        self._running_since = started
        self._record(scheduled_for=scheduled_for, result="running", started=started)
        logger.info("Scheduled daily update started (planned for %s)", _iso(scheduled_for))
        try:
            result = self._job()
        except Exception as exc:  # noqa: BLE001 - recorded and logged; the scheduler keeps running
            logger.exception("Scheduled daily update failed")
            result = f"error: {exc}"
        self._running_since = None
        self._record(scheduled_for=scheduled_for, result=result, started=started, finished=self._clock())
        logger.info("Scheduled daily update finished: %s", result)

    def _record(self, *, scheduled_for: datetime, result: str, started: Optional[datetime],
                finished: Optional[datetime] = None) -> None:
        self._state = {
            "scheduled_for": _iso(scheduled_for),
            "started_at": _iso(started),
            "finished_at": _iso(finished),
            "result": result,
        }
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
            tmp.replace(self._state_file)
        except OSError as exc:
            logger.warning("Cannot save scheduler state to %s: %s", self._state_file, exc)

    def _load_state(self) -> dict:
        try:
            raw = json.loads(self._state_file.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            logger.warning("Ignoring unreadable scheduler state %s: %s", self._state_file, exc)
            return {}
