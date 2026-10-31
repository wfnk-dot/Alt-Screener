"""Loads config.yml."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yml"


def load_config(path=DEFAULT_CONFIG):
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # One role to ping in every channel, if set (overrides each channel's own "ping")
    role = str(cfg.get("ping_role_id") or "").strip().strip("<@&>")
    if role.isdigit():
        for section in ("discord", "alerts", "events"):
            if section in cfg:
                cfg[section]["ping"] = f"<@&{role}>"
    return cfg
