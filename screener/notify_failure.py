"""Posts '⚠️ Screener failed: <reason>' to Discord. Standard library only, so it works even if installs failed."""
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        print("No DISCORD_WEBHOOK_URL secret; can't report the failure to Discord.")
        return 0
    err = ROOT / "run_error.txt"
    reason = err.read_text(encoding="utf-8").strip() if err.exists() else "a workflow step failed (see the GitHub Actions log)"
    body = json.dumps({"content": f"⚠️ Screener failed: {reason}"[:2000], "allowed_mentions": {"parse": []}}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": "alt-screener"})
    urllib.request.urlopen(req, timeout=30)
    return 0


if __name__ == "__main__":
    sys.exit(main())
