"""state.json: past weekly lists (for the new/dropped diff) and the last scheduled post."""
import json
from datetime import timedelta
from pathlib import Path


def load_state(path):
    p = Path(path)
    if not p.exists():
        return {"weeks": {}, "last_scheduled_week": None, "last_midweek_slot": None, "midweek_pinged": {}}
    data = json.loads(p.read_text(encoding="utf-8"))
    data.setdefault("weeks", {})
    data.setdefault("last_scheduled_week", None)
    data.setdefault("last_midweek_slot", None)
    data.setdefault("midweek_pinged", {})
    return data


def previous_list(state, week_key):
    """The most recent saved list from an earlier week, or None if there isn't one."""
    earlier = sorted(k for k in state["weeks"] if k < week_key)
    return state["weeks"][earlier[-1]] if earlier else None


def record_week(state, week_key, coins, keep_weeks, scheduled):
    state["weeks"][week_key] = list(coins)
    for k in sorted(state["weeks"])[:-keep_weeks]:
        del state["weeks"][k]
    if scheduled:
        state["last_scheduled_week"] = week_key
    return state


def save_state(path, state):
    Path(path).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def current_list(state, today):
    """The list that's live right now: the one saved for this week's Monday, else the latest saved."""
    monday = (today - timedelta(days=today.weekday())).isoformat()
    if monday in state["weeks"]:
        return monday, state["weeks"][monday]
    if state["weeks"]:
        latest = sorted(state["weeks"])[-1]
        return latest, state["weeks"][latest]
    return monday, []
