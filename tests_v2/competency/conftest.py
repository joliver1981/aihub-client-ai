"""Shared fixtures for competency-level tests.

Competency tests differ from journey / chaos / lifecycle tests:
  - They are not pass/fail. They are SCORED.
  - They run a battery of (file, question) pairs through a real agent
    and grade each answer with a regex list.
  - The verdict is a percentage and a dimension breakdown — "the agent
    is 85% competent at lookups but 40% competent at cross-sheet
    reasoning" — not "tests pass".
  - They take MINUTES, not seconds. We run them on demand, not in CI.

Each suite shares this conftest's `agent_for_competency` fixture which
provisions a brand-new test agent at session start, uploads the suite's
fixtures, waits for indexing, and DELETES the agent at session end.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

import pytest
import requests


# Unicode-safe stdout on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


MAIN_BASE = os.getenv("AI_HUB_BASE_URL", "http://localhost:5001")
API_KEY = os.getenv("AIHUB_API_KEY", "DB27D555-03A8-446E-9C23-8DAAA95EAD21")
# Index settle time after upload — competency uses larger files so we
# wait longer than journey tests.
INDEX_WAIT_SECONDS = int(os.getenv("COMPETENCY_INDEX_WAIT", "120"))

ARTIFACT_PREFIX = "COMP_"


@pytest.fixture(scope="session")
def services_ready():
    """Skip the whole suite if the main app on :5001 is unreachable."""
    try:
        r = requests.get(
            f"{MAIN_BASE}/get/workflows",
            headers={"X-API-Key": API_KEY},
            timeout=5,
        )
        if r.status_code != 200:
            pytest.skip(
                f"Main app at {MAIN_BASE} returned {r.status_code} for "
                f"/get/workflows. Competency suite requires a live stack."
            )
    except Exception as e:
        pytest.skip(f"Main app at {MAIN_BASE} unreachable: {e}")
    return True


@pytest.fixture(scope="session")
def http_session(services_ready) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "X-API-Key": API_KEY,
        "Authorization": f"Bearer {API_KEY}",
    })
    return s


def _safe_post_json(s, url, **kwargs):
    """POST returning (status, json_or_text)."""
    try:
        r = s.post(url, timeout=60, **kwargs)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text[:200]
    except Exception as e:
        return 0, f"err:{type(e).__name__}:{e}"


@pytest.fixture(scope="session")
def reports_dir() -> Path:
    p = Path(__file__).parent.parent / "artifacts" / "competency"
    p.mkdir(parents=True, exist_ok=True)
    return p
