"""Posts '⚠️ Screener failed: <reason>' to Discord. Standard library only, so it works even if installs failed."""
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def mark_week_done(week):
    """Stop the hourly timer retrying (and re-posting the failure) for the rest of the week."""
    path = ROOT / "state.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"weeks": {}}
    state["last_scheduled_week"] = week
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main():
    week = sys.argv[sys.argv.index("--mark-week") + 1] if "--mark-week" in sys.argv else None
    if week:
        mark_week_done(week)
    if "--mark-midweek" in sys.argv:
        week = sys.argv[sys.argv.index("--mark-midweek") + 1]  # also means "don't retry"
        path = ROOT / "state.json"
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"weeks": {}}
        state["last_midweek_slot"] = week
        path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        print("No DISCORD_WEBHOOK_URL secret; can't report the failure to Discord.")
        return 0
    err = ROOT / "run_error.txt"
    reason = err.read_text(encoding="utf-8").strip() if err.exists() else "a workflow step failed (see the GitHub Actions log)"
    retry = "\nIt won't retry by itself: press Run workflow on GitHub (Actions tab) to try again."
    body = json.dumps({"content": (f"⚠️ Screener failed: {reason}" + (retry if week else ""))[:2000], "allowed_mentions": {"parse": []}}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": "alt-screener"})
    urllib.request.urlopen(req, timeout=30)
    return 0


if __name__ == "__main__":
    sys.exit(main())
