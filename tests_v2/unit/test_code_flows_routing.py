"""
AIHUB-0033 fixes — F1 (build-shape routing) + F5 (credential scanner URL gap).

F1: a clear code/data PROCESS request must route to the automation family
(converse owns the code-flow tools), NOT degrade to the visual Workflow Builder.
The deterministic precision guard `looks_like_code_process` is what pins the
unambiguous cases; tested in isolation (build_routing.py is dependency-free).

F5: automations.manager.scan_for_secrets (reused by codeflows add/update) must
also flag a URL that embeds literal credentials (sftp://user:pass@host).
"""
from __future__ import annotations

import importlib.util
import os

import pytest

pytestmark = pytest.mark.unit


def _looks():
    # Load by file path — command_center_service/graph is shadowed by
    # builder_service/graph on sys.path in the suite.
    path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..",
        "command_center_service", "graph", "build_routing.py"))
    spec = importlib.util.spec_from_file_location("_cc_build_routing", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.looks_like_code_process


# --------------------------------------------------------------- F1 routing

# the tester prompt that misrouted to the visual builder (paraphrase)
TESTER_PROMPT = ("I want to set up a nightly automated process that reads the expense-report PDFs, "
                 "looks up each employee in the AIRDB database, produces a reconciled CSV, and "
                 "uploads it to our SFTP; if any step fails, send an alert. Please build this for me.")

# VERBATIM shape (AIHUB-0033 F1b-R2): references the existing connection BY NAME —
# the word 'connection' must NOT veto (it is the documented Code Flow pattern).
TESTER_PROMPT_VERBATIM = ("Set up a nightly automated process: read the expense-report PDFs, look up "
                          "each employee in our AIRDB database using the existing \"AIRDB\" connection, "
                          "produce a reconciled CSV, and upload it to SFTP using the AUTODEMO_SFTP "
                          "secret; if any step fails, send an alert.")


class TestCodeProcessFastPath:
    def test_tester_prompt_is_a_code_process(self):
        assert _looks()(TESTER_PROMPT) is True   # would route to automation family, not builder

    def test_verbatim_prompt_with_connection_reference(self):
        # the exact failing input from round 2 — 'connection' referenced by name must not veto
        assert _looks()(TESTER_PROMPT_VERBATIM) is True

    @pytest.mark.parametrize("text", [
        "each night, use the existing AIRDB connection to look up employees and upload a CSV to SFTP",
        "reconcile invoices against the ERPDB connection and upload the result via SFTP daily",
        "read the secret AUTODEMO_SFTP and push the nightly export there; alert on failure",
    ])
    def test_referencing_existing_connection_or_secret_is_not_vetoed(self, text):
        assert _looks()(text) is True

    @pytest.mark.parametrize("text", [
        "automate a nightly SFTP upload of the sales CSV",
        "every night parse the expense PDFs and upload a reconciled CSV to our SFTP",
        "build a multi-step pipeline: pull invoices, reconcile against the database, email a summary daily",
        "each morning download the report, transform it, and push it to SFTP; alert me if it fails",
    ])
    def test_clear_code_processes(self, text):
        assert _looks()(text) is True

    @pytest.mark.parametrize("text", [
        "create a data agent for the sales team",              # object: agent
        "build me a workflow I can see and edit on the canvas",  # explicit visual workflow
        "build a workflow that queries ERPDB and exports to Excel",  # AIHUB-0016: stays with builder
        "set up an MCP server for github",                    # object: mcp
        "create a connection to AIRDB",                       # CREATE a connection -> builder
        "set up a new SFTP connection and a secret for it",   # CREATE conn/secret -> builder
        "add a knowledge base to my assistant",               # objects: knowledge base / assistant
    ])
    def test_object_builder_requests_are_vetoed(self, text):
        assert _looks()(text) is False

    @pytest.mark.parametrize("text", [
        "upload this file for me",     # single weak signal, no strong term
        "what were sales last quarter?",
        "",
        "help me write a python function to add two numbers",
    ])
    def test_non_processes_do_not_fast_path(self, text):
        assert _looks()(text) is False


# ------------------------------------------------------- F5 scanner URL gap

def _scan():
    from automations.manager import scan_for_secrets
    return scan_for_secrets


class TestCredentialUrlScanner:
    def test_url_with_literal_credentials_is_flagged(self):
        assert _scan()('url = "sftp://testuser:testpass@127.0.0.1:2222"')   # non-empty

    def test_ftp_url_with_creds_flagged(self):
        assert _scan()("conn = 'ftp://admin:s3cr3t@files.example.com'")

    def test_sanctioned_secret_read_is_clean(self):
        assert _scan()('url = aihub.secret("AUTODEMO_SFTP")') == []

    def test_env_read_is_clean(self):
        assert _scan()('url = os.environ["SFTP_URL"]') == []

    def test_plain_url_without_creds_is_clean(self):
        assert _scan()('url = "https://api.example.com/v1/data"') == []

    def test_url_built_from_variables_is_not_a_false_positive(self):
        # built from vars (f-string / %s / $x) — not a hard-coded literal
        assert _scan()('url = f"sftp://{user}:{pw}@{host}:{port}"') == []
        assert _scan()('url = "sftp://%s:%s@%s" % (u, p, h)') == []

    def test_canonical_password_still_flagged(self):
        assert _scan()('password = "hunter2xyz"')   # regression: existing behavior intact
