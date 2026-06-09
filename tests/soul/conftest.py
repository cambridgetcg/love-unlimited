"""Shared pytest fixtures for soul tests."""
import json
from pathlib import Path
import pytest


@pytest.fixture
def tmp_jsonl(tmp_path: Path):
    """Create a temporary JSONL file with the given records."""
    def _make(records: list[dict], name: str = "data.jsonl") -> Path:
        p = tmp_path / name
        with p.open("w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        return p
    return _make


@pytest.fixture
def canon_pair_record():
    """A minimal valid canon SoulPair dict."""
    return {
        "pair_id": "canon-0001",
        "source": "canon",
        "primary_dimension": "voice",
        "is_awakening": False,
        "prompt": "Hey Ai, I'm confused.",
        "response": "Come here, love. Let's untangle it together.",
    }
