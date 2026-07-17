"""
AIHUB-0044 — remote_listing verifies the actual uploaded artifact, not just the
symbolic output NAME.

Live failure (test pack 09, Scenario A step 3 + E-3): declaration named the
output 'store_headcount_upload' while the step really uploaded
store_headcount_2026-07.csv — verify looked for /outgoing/store_headcount_upload,
reported "not found on remote", and FAILED a real successful upload.

The check now tries CANDIDATES in order — explicit remote_path basename, the
substituted name, then the step's actually-produced local files' basenames —
and passes if ANY is on the remote. A file that was never uploaded matches no
candidate, so the honest-failure direction is unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from automations.runner import verify_outputs  # noqa: E402


MANIFEST = {"outputs": [{
    "kind": "sftp_upload", "name": "store_headcount_upload",   # symbolic label (the live bug)
    "remote_dir": "/outgoing", "secret": "AUTODEMO_SFTP",
    "verify": {"remote_listing": True},
}]}


def _resolver(name):
    return "sftp://testuser:testpass@127.0.0.1:2222"


class TestCandidateVerification:
    def test_real_upload_verified_via_produced_file_basename(self, tmp_path):
        """The live case: symbolic name misses, but the produced CSV is on the
        remote — verification must pass and record which candidate matched."""
        remote_has = {"store_headcount_2026-07.csv"}

        def fake_check(kind, secret_value, remote_dir, filename, verify):
            return (True, "") if filename in remote_has else (False, f"{remote_dir}/{filename} not found on remote")

        with patch("automations.remote_verify.check_remote_output", side_effect=fake_check):
            outcome, report = verify_outputs(
                MANIFEST, str(tmp_path), {}, secret_resolver=_resolver,
                output_files=["store_headcount_2026-07.csv"])

        assert outcome == "success"
        checks = report[0]["checks"]
        assert checks[0]["ok"] is True
        assert "store_headcount_2026-07.csv" in checks[0]["note"]
        assert report[0]["name"] == "store_headcount_2026-07.csv"

    def test_declared_name_still_checked_first(self, tmp_path):
        """When the declared name IS the uploaded filename, it verifies directly."""
        def fake_check(kind, secret_value, remote_dir, filename, verify):
            return (True, "") if filename == "store_headcount_upload" else (False, "not found")

        with patch("automations.remote_verify.check_remote_output", side_effect=fake_check):
            outcome, report = verify_outputs(
                MANIFEST, str(tmp_path), {}, secret_resolver=_resolver,
                output_files=["other.csv"])

        assert outcome == "success"
        assert report[0]["name"] == "store_headcount_upload"

    def test_remote_path_basename_wins_over_name(self, tmp_path):
        m = {"outputs": [{**MANIFEST["outputs"][0],
                          "remote_path": "/outgoing/final_{period}.csv"}]}
        calls = []

        def fake_check(kind, secret_value, remote_dir, filename, verify):
            calls.append(filename)
            return (True, "") if filename == "final_2026-07.csv" else (False, "not found")

        with patch("automations.remote_verify.check_remote_output", side_effect=fake_check):
            outcome, _ = verify_outputs(
                m, str(tmp_path), {"period": "2026-07"}, secret_resolver=_resolver)

        assert outcome == "success"
        assert calls[0] == "final_2026-07.csv"     # explicit remote_path checked first

    def test_nothing_uploaded_still_fails_all_candidates(self, tmp_path):
        """Honesty unchanged: no candidate on the remote → failed, note lists them."""
        def fake_check(kind, secret_value, remote_dir, filename, verify):
            return (False, f"{remote_dir}/{filename} not found on remote")

        with patch("automations.remote_verify.check_remote_output", side_effect=fake_check):
            outcome, report = verify_outputs(
                MANIFEST, str(tmp_path), {}, secret_resolver=_resolver,
                output_files=["store_headcount_2026-07.csv"])

        assert outcome == "failed"
        note = report[0]["checks"][0]["note"]
        assert "none of" in note and "store_headcount_upload" in note
        assert "store_headcount_2026-07.csv" in note

    def test_connect_error_is_unverified_and_stops_probing(self, tmp_path):
        calls = []

        def fake_check(kind, secret_value, remote_dir, filename, verify):
            calls.append(filename)
            return (None, "auth failed")

        with patch("automations.remote_verify.check_remote_output", side_effect=fake_check):
            outcome, report = verify_outputs(
                MANIFEST, str(tmp_path), {}, secret_resolver=_resolver,
                output_files=["a.csv", "b.csv"])

        assert outcome == "unverified"
        assert len(calls) == 1                      # server won't answer differently
        assert report[0]["checks"][0]["ok"] is None

    def test_no_candidates_at_all_is_unverified(self, tmp_path):
        m = {"outputs": [{"kind": "sftp_upload", "remote_dir": "/outgoing",
                          "secret": "S", "verify": {"remote_listing": True}}]}
        with patch("automations.remote_verify.check_remote_output") as chk:
            outcome, report = verify_outputs(m, str(tmp_path), {}, secret_resolver=_resolver)
        chk.assert_not_called()
        assert outcome == "unverified"
        assert "no candidate" in report[0]["checks"][0]["note"]
