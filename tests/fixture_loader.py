from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def load_sample(name: str) -> Any:
    """Read a payload fixture under tests/fixtures by file stem."""
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
