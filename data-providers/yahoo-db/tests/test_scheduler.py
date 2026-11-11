"""Daily scheduler tests (run: python -m pytest tests -q from the yahoo-db folder)."""

import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scheduler import DailyScheduler, ScheduleConfig, next_run, previous_run  # noqa: E402

NY = ScheduleConfig(run_time="23:30", timezone="America/New_York")


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def test_next_run_is_today_or_tomorrow_in_new_york_time():
    # 2026-11-11 is EST (UTC-5): 23:30 New York = 04:30 UTC the next day.
    assert next_run(NY, utc("2026-11-11T20:00:00")) == utc("2026-11-12T04:30:00")
    assert next_run(NY, utc("2026-11-12T04:30:00")) == utc("2026-11-13T04:30:00")
    assert previous_run(NY, utc("2026-11-12T04:30:00")) == utc("2026-11-12T04:30:00")
    assert previous_run(NY, utc("2026-11-12T04:29:00")) == utc("2026-11-11T04:30:00")


def test_daylight_saving_time_keeps_the_wall_clock_time():
    # Summer (EDT, UTC-4): 23:30 New York = 03:30 UTC.
    assert next_run(NY, utc("2026-07-01T12:00:00")) == utc("2026-07-02T03:30:00")
    # Across the November change (Nov 1, 2026) the UTC time moves by one hour.
    assert next_run(NY, utc("2026-10-31T12:00:00")) == utc("2026-11-01T03:30:00")
    assert next_run(NY, utc("2026-11-01T12:00:00")) == utc("2026-11-02T04:30:00")


@pytest.mark.parametrize("raw, message", [
    ({"run_time": "24:00"}, "HH:MM"),
    ({"run_time": "7:30"}, "HH:MM"),
    ({"timezone": "Mars/Olympus"}, "Unknown time zone"),
    ({"enabled": "yes"}, "true or false"),
    ({"speed": 1}, "Unknown schedule field"),
    ([], "JSON object"),
])
def test_invalid_config_is_rejected(raw, message):
    with pytest.raises(ValueError, match=message):
        ScheduleConfig.from_dict(raw)


def test_partial_update_keeps_other_fields():
    base = ScheduleConfig(enabled=True, run_time="23:30", timezone="UTC", catch_up=False)
    updated = ScheduleConfig.from_dict({"run_time": "06:15"}, defaults=base)
    assert updated == ScheduleConfig(enabled=True, run_time="06:15", timezone="UTC", catch_up=False)
    assert ScheduleConfig.from_dict({"timezone": "local"}).timezone == "local"


def _scheduler(tmp_path, clock, job, config=NY, **kwargs):
    return DailyScheduler(config, job, tmp_path / "state.json", clock=clock, **kwargs)


def test_first_start_does_not_run_a_missed_update(tmp_path):
    clock = Clock(utc("2026-11-12T10:00:00"))
    scheduler = _scheduler(tmp_path, clock, lambda: "completed")
    scheduler.start()
    try:
        status = scheduler.status()
        assert status["next_run"] == "2026-11-13T04:30:00+00:00"
        assert not status["next_run_is_catch_up"]
        assert json.loads((tmp_path / "state.json").read_text())["scheduled_for"] == "2026-11-12T04:30:00+00:00"
    finally:
        scheduler.stop()


def test_missed_update_is_caught_up_after_restart(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"scheduled_for": "2026-11-11T04:30:00+00:00", "result": "completed"}))
    clock = Clock(utc("2026-11-12T10:00:00"))  # the 04:30 UTC run of today was missed
    scheduler = _scheduler(tmp_path, clock, lambda: "completed", catch_up_delay=60)
    scheduler.start()
    try:
        status = scheduler.status()
        assert status["next_run"] == "2026-11-12T10:01:00+00:00" and status["next_run_is_catch_up"]
    finally:
        scheduler.stop()


def test_no_catch_up_when_disabled(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"scheduled_for": "2026-11-11T04:30:00+00:00"}))
    clock = Clock(utc("2026-11-12T10:00:00"))
    config = ScheduleConfig(run_time="23:30", timezone="America/New_York", catch_up=False)
    scheduler = _scheduler(tmp_path, clock, lambda: "completed", config=config)
    scheduler.start()
    try:
        assert scheduler.status()["next_run"] == "2026-11-13T04:30:00+00:00"
    finally:
        scheduler.stop()


def test_job_runs_at_the_scheduled_time_and_records_the_result(tmp_path):
    clock = Clock(utc("2026-11-12T04:29:59"))
    ran = threading.Event()

    def job():
        ran.set()
        clock.now += timedelta(minutes=2)  # the update takes a while
        return "completed"

    scheduler = _scheduler(tmp_path, clock, job)
    scheduler.start()
    try:
        assert not ran.wait(0.3)
        clock.now = utc("2026-11-12T04:30:00")
        scheduler.update(NY)  # wakes the thread instead of waiting for the next minute check
        assert ran.wait(5)
        for _ in range(50):
            if scheduler.status()["last_run"]["result"] == "completed":
                break
            threading.Event().wait(0.05)
        last = scheduler.status()["last_run"]
        assert last["scheduled_for"] == "2026-11-12T04:30:00+00:00" and last["result"] == "completed"
        assert scheduler.status()["next_run"] == "2026-11-13T04:30:00+00:00"
    finally:
        scheduler.stop()


def test_failing_job_is_recorded_and_scheduler_keeps_running(tmp_path):
    clock = Clock(utc("2026-11-12T04:29:00"))

    def job():
        clock.now += timedelta(minutes=1)
        raise RuntimeError("Yahoo down")

    scheduler = _scheduler(tmp_path, clock, job)
    scheduler.start()
    try:
        clock.now = utc("2026-11-12T04:30:00")
        scheduler.update(NY)
        for _ in range(100):
            if (scheduler.status()["last_run"].get("result") or "").startswith("error"):
                break
            threading.Event().wait(0.05)
        assert scheduler.status()["last_run"]["result"] == "error: Yahoo down"
        assert scheduler._thread.is_alive()
    finally:
        scheduler.stop()
