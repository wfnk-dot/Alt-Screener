"""Loads config.yml."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yml"


def load_config(path=DEFAULT_CONFIG):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)
