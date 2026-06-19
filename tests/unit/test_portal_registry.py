"""
Unit tests for the portal registry (local JSON + encrypted-secret references).
=============================================================================
Fully isolated: the registry path is redirected to a tmp file and local_secrets
set/get are stubbed with an in-memory dict, so the real encrypted store and the
real data/portal_registry.json are never touched.

Run:
    python -m pytest tests/unit/test_portal_registry.py -v
"""
import json
import sys

import pytest

sys.path.insert(0, r"C:/src/aihub-client-ai-dev")

from command_center.tools import portal_registry as reg  # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Registry -> tmp file; local_secrets -> in-memory dict."""
    store = {}
    monkeypatch.setattr(reg, "_registry_path", lambda: str(tmp_path / "portal_registry.json"))
    import local_secrets
    monkeypatch.setattr(local_secrets, "set_local_secret",
                        lambda n, v, category=None: store.__setitem__(n, v), raising=False)
    monkeypatch.setattr(local_secrets, "get_local_secret",
                        lambda n, default="": store.get(n, default), raising=False)
    return store


# --------------------------------------------------------------------------
# slug + key-name derivation
# --------------------------------------------------------------------------

def test_slug_normalization():
    assert reg.slug("Acme Vendor, Inc.") == "acme_vendor_inc"
    assert reg.slug("  Portal X!  ") == "portal_x"
    assert reg.slug("acme") == "acme"


def test_secret_key_names_are_user_scoped():
    k = reg.secret_key_names(13, "Acme Vendor")
    assert k["username_secret"] == "PORTAL_U13_ACME_VENDOR_USERNAME"
    assert k["password_secret"] == "PORTAL_U13_ACME_VENDOR_PASSWORD"
    assert k["totp_secret"] == "PORTAL_U13_ACME_VENDOR_TOTP"
    # different user -> different keys (no cross-user collision)
    assert reg.secret_key_names(99, "Acme Vendor")["username_secret"] == \
        "PORTAL_U99_ACME_VENDOR_USERNAME"


# --------------------------------------------------------------------------
# save -> lookup -> list
# --------------------------------------------------------------------------

def test_save_then_lookup_and_list(isolated):
    store = isolated
    entry = reg.save_portal(13, "Acme Vendor Portal", "https://portal.acme.com/login",
                            "jsmith", "S3cr3t!", totp=None)
    assert entry["slug"] == "acme_vendor_portal"

    # credentials went to the (stubbed) encrypted store under user-scoped keys
    assert store["PORTAL_U13_ACME_VENDOR_PORTAL_USERNAME"] == "jsmith"
    assert store["PORTAL_U13_ACME_VENDOR_PORTAL_PASSWORD"] == "S3cr3t!"

    # the registry json holds references, NEVER the raw secret
    raw = json.dumps(reg._load())
    assert "S3cr3t!" not in raw and "jsmith" not in raw
    assert "PORTAL_U13_ACME_VENDOR_PORTAL_PASSWORD" in raw

    # lookup resolves by a loose name and returns the key-name references + url
    got = reg.lookup_portal(13, "the acme vendor portal")
    assert got["url"] == "https://portal.acme.com/login"
    assert got["password_secret"] == "PORTAL_U13_ACME_VENDOR_PORTAL_PASSWORD"

    # list returns metadata only
    assert [p["name"] for p in reg.list_portals(13)] == ["Acme Vendor Portal"]


def test_per_user_isolation(isolated):
    reg.save_portal(13, "acme", "https://acme.com", "u", "p")
    assert reg.lookup_portal(13, "acme") is not None
    # a different user cannot see user 13's saved portal
    assert reg.lookup_portal(99, "acme") is None
    assert reg.list_portals(99) == []


def test_totp_optional(isolated):
    store = isolated
    reg.save_portal(13, "acme", "https://acme.com", "u", "p", totp="JBSWY3DPEHPK3PXP")
    assert store["PORTAL_U13_ACME_TOTP"] == "JBSWY3DPEHPK3PXP"
    assert reg.lookup_portal(13, "acme")["totp_secret"] == "PORTAL_U13_ACME_TOTP"


def test_delete_portal(isolated):
    reg.save_portal(13, "acme", "https://acme.com", "u", "p")
    assert reg.delete_portal(13, "acme") is True
    assert reg.lookup_portal(13, "acme") is None
    assert reg.delete_portal(13, "acme") is False  # already gone
