"""Landscape scanner cache is keyed per identity (2026-09-22).

A regular user's landscape is scoped to their groups (connection ACL + agent
visibility), so the 60-second cache must not hand one seat's view to another:
live, a no-group seat inherited another seat's ERPDB through the shared entry.
Developers / admins keep sharing the unrestricted '*' entry.
Force-add to git (gitignore hides test*.py).
"""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from command_center.orchestration import landscape_scanner as ls  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("ctx,key", [
    ({"user_id": 352, "role": 1}, "u:352"),
    ({"user_id": "349", "role": "1"}, "u:349"),
    ({"user_id": 353, "role": 2}, "*"),
    ({"user_id": 13, "role": 3}, "*"),
    ({"user_id": 0, "role": 1}, "*"),
    ({"user_id": "anonymous", "role": 1}, "*"),
    ({}, "*"),
    (None, "*"),
    ("garbage", "*"),
])
def test_cache_key_by_identity(ctx, key):
    assert ls.landscape_cache_key(ctx) == key


def test_entries_do_not_cross_users():
    ls._caches.clear()
    ls._caches[ls.landscape_cache_key({"user_id": 349, "role": 1})] = ({"agents": ["alex"]}, 1e12)
    assert ls._caches.get(ls.landscape_cache_key({"user_id": 352, "role": 1})) is None
    assert ls._caches.get(ls.landscape_cache_key({"user_id": 353, "role": 2})) is None
    assert ls._caches[ls.landscape_cache_key({"user_id": "349", "role": 1})][0] == {"agents": ["alex"]}
    ls._caches.clear()
