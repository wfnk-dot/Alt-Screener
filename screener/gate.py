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


def midweek_slot_due(now, sched, last_midweek_slot):
    """The mid-week slot to run now (ISO string), or None. Same 24-hour catch-up as the weekly post."""
    for day in sched.get("midweek_days") or []:
        slot = last_slot(now, day, sched.get("midweek_time", sched["time"]))
        if now - slot < timedelta(hours=24) and slot.isoformat() != last_midweek_slot:
            return slot.isoformat()
    return None


def main():
    cfg = load_config()
    sched = cfg["schedule"]
    now = datetime.now(ZoneInfo(sched["timezone"]))
    state = load_state(ROOT / "state.json")
    week = week_of(last_slot(now, sched["day"], sched["time"]).date()).isoformat()
    mode, slot = "", ""
    if should_run(now, sched, state["last_scheduled_week"]):
        mode = "weekly"
    else:
        slot = midweek_slot_due(now, sched, state.get("last_midweek_slot")) or ""
        mode = "midweek" if slot else ""
    run = bool(mode)
    print(f"UK time {now:%a %H:%M} -> run={'true' if run else 'false'} {mode}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"run={'true' if run else 'false'}\nmode={mode}\nweek={week}\nslot={slot}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
