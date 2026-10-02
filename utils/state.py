"""Persists last-known stock status per product so we only alert on transitions."""
import json
import logging
from pathlib import Path

from utils.config import PROJECT_ROOT

log = logging.getLogger(__name__)
STATE_PATH = PROJECT_ROOT / "data" / "state.json"


def load_state(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        state = json.loads(path.read_text())
    except json.JSONDecodeError:
        # A half-written file (e.g. Pi lost power) shouldn't crash the bot on boot.
        log.warning("State file %s is corrupt; starting fresh", path)
        return {}
    # Older versions stored {"product_id": true}; upgrade to the dict format.
    return {k: v if isinstance(v, dict) else {"in_stock": bool(v)} for k, v in state.items()}


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(path)  # atomic rename, so the file is never half-written
