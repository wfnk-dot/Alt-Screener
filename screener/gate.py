"""Decides whether an hourly scheduled wake-up should run the scan.

GitHub's timer only speaks UTC and is often late, so the workflow wakes every hour
and this checks UK time against config.yml. It runs once per week: the first
wake-up at or after the post time, up to 24 hours late.
"""
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import ROOT, load_config
from .output import week_of
from .state import load_state

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def last_slot(now, day_name, hhmm):
    """The most recent scheduled post time at or before `now` (same time zone as `now`)."""
    hour, minute = (int(x) for x in str(hhmm).split(":"))
    target = DAYS.index(day_name.strip().lower())
    back = (now.weekday() - target) % 7
    slot = (now - timedelta(days=back)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot > now:
        slot -= timedelta(days=7)
    return slot


def should_run(now, sched, last_scheduled_week):
    slot = last_slot(now, sched["day"], sched["time"])
    if now - slot >= timedelta(hours=24):
        return False
    return last_scheduled_week != week_of(slot.date()).isoformat()


def main():
    cfg = load_config()
    now = datetime.now(ZoneInfo(cfg["schedule"]["timezone"]))
    run = should_run(now, cfg["schedule"], load_state(ROOT / "state.json")["last_scheduled_week"])
    print(f"UK time {now:%a %H:%M} -> run={'true' if run else 'false'}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"run={'true' if run else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
