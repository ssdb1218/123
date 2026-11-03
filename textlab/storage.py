"""Local plaintext audit records. No API credentials are stored."""
from pathlib import Path
import json
import os
import tempfile

from .models import RunState


def save_state(state: RunState, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2, allow_nan=False)
    fd, name = tempfile.mkstemp(prefix=".run-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
        os.replace(name, directory / f"{state.id}.json")
    finally:
        if os.path.exists(name):
            os.unlink(name)
