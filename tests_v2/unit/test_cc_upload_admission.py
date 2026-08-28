"""
Admit-or-deny policy, P2 (2026-08-25) — Command Center chat uploads.

Covers, on a fresh FastAPI app with the upload router and a tmp uploads dir:
- eager extraction at upload with a FULL-fidelity cache (no more permanent
  truncated _analysis.txt)
- per-file deny → HTTP 413 with numbers; nothing left on disk or in the store
- tabular bypass (huge CSV admitted)
- session attachment budget deny
- soft warning surfaced in the upload response
- stale (pre-policy) truncated caches are re-extracted; a legacy over-ceiling
  cache is NOT re-extracted forever
- meta sidecars: ownership survives _reconstruct_file_store
- run_python availability mirror + the tabular honesty note when the
  interpreter is off for the user's role
- upload identity hardening (2026-08-28): CC_REQUIRE_JWT enforcement on
  upload/list/delete, JWT claims overriding forged form identity, and
  ownership-gated deletes
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_SVC_ROOT = _ROOT / "command_center_service"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
sys.path = [str(_SVC_ROOT)] + [p for p in sys.path if p != str(_SVC_ROOT)]
for _m in [m for m in list(sys.modules) if m == "routes" or m.startswith("routes.")]:
    del sys.modules[_m]

from routes import upload as up  # noqa: E402


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """Fresh app + isolated uploads dir + cleared store + fake extractor.

    Runs in CC_REQUIRE_JWT=0 shadow mode so the admission tests keep driving
    identity through the legacy form fields; TestUploadIdentity flips
    enforcement back on per-test."""
    monkeypatch.setenv("CC_REQUIRE_JWT", "0")
    monkeypatch.setattr(up, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(up, "_file_store", {})

    # Fake extractor: returns the file's own text so tests control size.
    def fake_extract(file_bytes, filename, content_type=None, max_chars=None,
                     allow_ocr_fallback=True):
        text = file_bytes.decode("utf-8", errors="replace")
        truncated = max_chars is not None and len(text) > max_chars
        if truncated:
            text = text[:max_chars]
        return {"success": True, "text": text, "truncated": truncated,
                "extraction_method": "fake"}

    import attachment_text_extractor as ate
    monkeypatch.setattr(ate, "extract_text_from_attachment", fake_extract)

    app = FastAPI()
    app.include_router(up.router)
    return TestClient(app), tmp_path


def _post(client, name, content: bytes, session_id="sess-1", user_id=7):
    return client.post(
        "/api/upload",
        files=[("files", (name, content, "text/plain"))],
        data={"session_id": session_id, "user_id": str(user_id), "tenant_id": "0"},
    )


class TestAdmissionAtUpload:
    def test_small_file_admitted_with_full_cache_and_sidecar(self, app_client):
        client, tmp = app_client
        r = _post(client, "notes.txt", b"hello world " * 100)
        assert r.status_code == 200
        fid = r.json()["files"][0]["file_id"]
        assert fid in up._file_store
        assert up._file_store[fid]["user_id"] == 7
        assert (tmp / f"{fid}_analysis.txt").exists()      # eager cache
        assert (tmp / f"{fid}_meta.json").exists()         # ownership sidecar
        assert "warning" not in r.json()["files"][0]

    def test_over_ceiling_denied_413_nothing_left(self, app_client):
        client, tmp = app_client
        big = b"x" * 1_300_000  # > 300K tokens * 4 chars
        r = _post(client, "big.txt", big)
        assert r.status_code == 413
        detail = r.json()["detail"]
        assert "NOT added" in detail and "300,000" in detail
        assert up._file_store == {}                        # not registered
        assert not any(p.name.endswith("big.txt") for p in tmp.iterdir())
        assert not list(tmp.glob("*_analysis.txt"))        # cache cleaned

    def test_huge_csv_admitted_via_tabular_bypass(self, app_client):
        client, _ = app_client
        big_csv = b"a,b\n" + b"1,2\n" * 400_000            # ~1.6M chars
        r = _post(client, "data.csv", big_csv)
        assert r.status_code == 200

    def test_session_budget_denied(self, app_client):
        client, tmp = app_client
        # Seed a prior admitted file for the session: 2.3M cached chars
        up._file_store["prior-000-abc"] = {
            "file_id": "prior-000-abc", "filename": "prior.txt",
            "session_id": "sess-1", "size": 1, "content_type": "text/plain",
            "path": str(tmp / "prior-000-abc_prior.txt"),
        }
        (tmp / "prior-000-abc_analysis.txt").write_text("y" * 2_300_000,
                                                        encoding="utf-8")
        r = _post(client, "more.txt", b"z" * 200_000, session_id="sess-1")
        assert r.status_code == 413
        assert "attachment budget" in r.json()["detail"]

    def test_soft_warning_surfaced(self, app_client):
        client, _ = app_client
        r = _post(client, "large.txt", b"w" * 700_000)     # ~175K tokens
        assert r.status_code == 200
        assert "large" in r.json()["files"][0]["warning"].lower()


class TestStaleCacheRepair:
    def _meta(self, tmp, fid, name):
        p = tmp / f"{fid}_{name}"
        return {"file_id": fid, "filename": name, "path": str(p),
                "content_type": "text/plain", "size": p.stat().st_size}

    def test_pre_policy_truncated_cache_reextracted(self, app_client):
        client, tmp = app_client
        fid = "aaaaaaaa-bbb"
        (tmp / f"{fid}_doc.txt").write_bytes(b"FULL CONTENT " * 1000)
        (tmp / f"{fid}_analysis.txt").write_text(
            "partial text\n\n[... Content truncated. 90,000 more characters not shown]",
            encoding="utf-8")
        out = up._extract_and_cache(fid, self._meta(tmp, fid, "doc.txt"))
        assert "FULL CONTENT" in out["content"]            # re-extracted
        assert "Content truncated" not in out["content"]

    def test_legacy_over_ceiling_cache_kept(self, app_client, monkeypatch):
        client, tmp = app_client
        fid = "cccccccc-ddd"
        (tmp / f"{fid}_doc.txt").write_bytes(b"irrelevant")
        near_ceiling = "z" * (up._extraction_ceiling_chars() - 10)
        (tmp / f"{fid}_analysis.txt").write_text(
            near_ceiling + "[... Content truncated. 5 more characters not shown]",
            encoding="utf-8")
        out = up._extract_and_cache(fid, self._meta(tmp, fid, "doc.txt"))
        assert out["method"] == "cached"                   # no re-extract loop


class TestSidecarReconstruction:
    def test_ownership_survives_restart(self, app_client):
        client, tmp = app_client
        r = _post(client, "mine.txt", b"data", user_id=42)
        fid = r.json()["files"][0]["file_id"]
        up._file_store.clear()
        up._reconstruct_file_store()
        assert up._file_store[fid]["user_id"] == 42        # was None pre-fix
        assert up._file_store[fid]["session_id"] == "sess-1"


def _token(monkeypatch, user_id, role=1, tenant_id=0):
    """Mint a real CC session JWT against a test secret."""
    pytest.importorskip("jwt")
    monkeypatch.setenv("CC_JWT_SECRET", "unit-test-secret-abcdef0123456789")
    import shared_auth
    return shared_auth.sign_cc_token({
        "user_id": user_id, "role": role, "tenant_id": tenant_id,
        "username": f"u{user_id}", "name": f"User {user_id}"})


class TestUploadIdentity:
    def test_enforced_routes_401_without_token(self, app_client, monkeypatch):
        client, _ = app_client
        monkeypatch.setenv("CC_REQUIRE_JWT", "1")
        r = _post(client, "denied.txt", b"data")
        assert r.status_code == 401
        assert up._file_store == {}
        assert client.get("/api/uploads").status_code == 401
        assert client.delete("/api/uploads/whatever-000").status_code == 401

    def test_jwt_claims_override_forged_form_identity(self, app_client, monkeypatch):
        client, _ = app_client
        monkeypatch.setenv("CC_REQUIRE_JWT", "1")
        tok = _token(monkeypatch, user_id=7, role=1, tenant_id=0)
        r = client.post(
            "/api/upload",
            files=[("files", ("mine.txt", b"payload", "text/plain"))],
            data={"session_id": "sess-1", "user_id": "999", "tenant_id": "5"},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 200
        fid = r.json()["files"][0]["file_id"]
        assert up._file_store[fid]["user_id"] == 7          # claims win
        assert up._file_store[fid]["tenant_id"] == 0

    def test_delete_is_ownership_gated(self, app_client, monkeypatch):
        client, tmp = app_client
        monkeypatch.setenv("CC_REQUIRE_JWT", "1")
        tok7 = _token(monkeypatch, user_id=7)
        r = client.post(
            "/api/upload",
            files=[("files", ("keep.txt", b"data", "text/plain"))],
            data={"session_id": "sess-1"},
            headers={"Authorization": f"Bearer {tok7}"},
        )
        fid = r.json()["files"][0]["file_id"]
        tok8 = _token(monkeypatch, user_id=8)
        r = client.delete(f"/api/uploads/{fid}",
                          headers={"Authorization": f"Bearer {tok8}"})
        assert r.status_code == 404                         # denied, not leaked
        assert fid in up._file_store                        # still there
        r = client.delete(f"/api/uploads/{fid}",
                          headers={"Authorization": f"Bearer {tok7}"})
        assert r.status_code == 200
        assert fid not in up._file_store

    def test_listing_scoped_to_caller(self, app_client, monkeypatch):
        client, _ = app_client
        monkeypatch.setenv("CC_REQUIRE_JWT", "1")
        for uid, name in ((7, "seven.txt"), (8, "eight.txt")):
            tok = _token(monkeypatch, user_id=uid)
            client.post(
                "/api/upload",
                files=[("files", (name, b"data", "text/plain"))],
                data={"session_id": "sess-1"},
                headers={"Authorization": f"Bearer {tok}"},
            )
        tok7 = _token(monkeypatch, user_id=7)
        names = {f["filename"] for f in client.get(
            "/api/uploads", headers={"Authorization": f"Bearer {tok7}"}
        ).json()["files"]}
        assert names == {"seven.txt"}


class TestRunPythonHonesty:
    def test_gate_matrix(self, monkeypatch):
        monkeypatch.setenv("CODE_INTERPRETER_ENABLED", "true")
        monkeypatch.setenv("CODE_INTERPRETER_ALLOW_ALL_USERS", "false")
        assert up._run_python_available(role=2)
        assert not up._run_python_available(role=0)
        monkeypatch.setenv("CODE_INTERPRETER_ENABLED", "false")
        assert not up._run_python_available(role=2)

    def test_tabular_note_when_interpreter_off_for_role(self, app_client, monkeypatch):
        client, tmp = app_client
        monkeypatch.setenv("CODE_INTERPRETER_ALLOW_ALL_USERS", "false")
        r = _post(client, "rows.csv", b"a,b\n1,2\n")
        fid = r.json()["files"][0]["file_id"]
        ctx = up.build_attachment_context([fid], user_id=7, tenant_id=0, role=0)
        assert "code interpreter is disabled" in ctx
        ctx_dev = up.build_attachment_context([fid], user_id=7, tenant_id=0, role=2)
        assert "code interpreter is disabled" not in ctx_dev
