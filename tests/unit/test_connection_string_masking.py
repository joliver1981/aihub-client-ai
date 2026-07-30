"""
Connection-string password masking + restore (masked-password save bug).
=========================================================================
Pins the helpers behind two fixes in /add/connection and /get/connections:

  * The BUG: the browser only ever sees 'Pwd=••••••••'; saving a connection
    echoed that masked string back and it was persisted verbatim — Test
    Connection passed (it substitutes), but discovery/data agents failed auth
    with the literal dots. /add/connection now restores the stored credential
    via swap_connection_string_password().
  * The LEAK: /get/connections previously sent connection_string verbatim,
    exposing legacy plaintext passwords to the browser. It now masks values
    via mask_connection_string_password().

The round-trip contract: mask on the way out -> the masked indicator comes
back on save -> swap restores the stored password column value (a
{{LOCAL_SECRET:...}} reference for migrated connections), which every runtime
consumer resolves via resolve_connection_string_secrets().
"""

import sys

import pytest

sys.path.insert(0, r"C:/src/aihub-client-ai-dev")

from connection_secrets import (  # noqa: E402
    MASKED_PASSWORD,
    connection_string_has_masked_password,
    mask_connection_string_password,
    resolve_connection_string_secrets,
    swap_connection_string_password,
)

PG = "DRIVER={PostgreSQL Unicode(x64)};Server=10.0.0.6;Port=5432;Database=EDWDB;Uid=postgres;Pwd=s3cret!;"
MSSQL = "DRIVER={ODBC Driver 17 for SQL Server};Server=sql01,1433;Database=app;Uid=ai_user;Password=p@ss;"
REF = "{{LOCAL_SECRET:CONN_PWD_154}}"


# ---------------------------------------------------------------------------
# mask_connection_string_password
# ---------------------------------------------------------------------------

def test_mask_pwd_value():
    masked = mask_connection_string_password(PG)
    assert f"Pwd={MASKED_PASSWORD};" in masked
    assert "s3cret!" not in masked
    # everything else untouched
    assert "Server=10.0.0.6" in masked and "Uid=postgres" in masked


def test_mask_password_key_variant():
    masked = mask_connection_string_password(MSSQL)
    assert f"Password={MASKED_PASSWORD};" in masked
    assert "p@ss" not in masked


def test_mask_is_case_insensitive_on_key():
    masked = mask_connection_string_password("Server=s;PWD=topsecret;")
    assert f"PWD={MASKED_PASSWORD};" in masked
    assert "topsecret" not in masked


def test_mask_empty_value_left_empty():
    s = "DRIVER={X};Server=s;Pwd=;Database=d;"
    assert mask_connection_string_password(s) == s


def test_mask_secret_reference_fully():
    s = f"DRIVER={{X}};Server=s;Pwd={REF};Database=d;"
    masked = mask_connection_string_password(s)
    assert f"Pwd={MASKED_PASSWORD};" in masked
    assert "LOCAL_SECRET" not in masked
    # no stray brace left behind from the double-braced reference
    assert f"{MASKED_PASSWORD}}}" not in masked


def test_mask_brace_quoted_password():
    s = "DRIVER={X};Server=s;Pwd={p;w};Database=d;"
    masked = mask_connection_string_password(s)
    assert f"Pwd={MASKED_PASSWORD};Database=d;" in masked
    assert "p;w" not in masked


def test_mask_handles_none_and_empty():
    assert mask_connection_string_password(None) is None
    assert mask_connection_string_password("") == ""


def test_mask_already_masked_is_stable():
    masked = mask_connection_string_password(PG)
    assert mask_connection_string_password(masked) == masked


# ---------------------------------------------------------------------------
# connection_string_has_masked_password
# ---------------------------------------------------------------------------

def test_detects_masked_password():
    assert connection_string_has_masked_password(mask_connection_string_password(PG))
    assert connection_string_has_masked_password(f"Server=s;Password={MASKED_PASSWORD};")


def test_no_false_positive_on_real_values():
    assert not connection_string_has_masked_password(PG)
    assert not connection_string_has_masked_password(f"Server=s;Pwd={REF};")
    assert not connection_string_has_masked_password("")
    assert not connection_string_has_masked_password(None)


# ---------------------------------------------------------------------------
# swap_connection_string_password
# ---------------------------------------------------------------------------

def test_swap_restores_masked_with_reference():
    masked = mask_connection_string_password(PG)
    restored = swap_connection_string_password(masked, MASKED_PASSWORD, REF)
    assert f"Pwd={REF};" in restored
    assert MASKED_PASSWORD not in restored


def test_swap_only_exact_value():
    # a different real password must not be clobbered
    restored = swap_connection_string_password(PG, MASKED_PASSWORD, REF)
    assert restored == PG


def test_swap_plaintext_for_reference():
    swapped = swap_connection_string_password(PG, "s3cret!", REF)
    assert f"Pwd={REF};" in swapped
    assert "s3cret!" not in swapped


def test_swap_handles_password_key_variant():
    masked = mask_connection_string_password(MSSQL)
    restored = swap_connection_string_password(masked, MASKED_PASSWORD, "newpw")
    assert "Password=newpw;" in restored


def test_swap_noop_on_empty_inputs():
    assert swap_connection_string_password("", MASKED_PASSWORD, "x") == ""
    assert swap_connection_string_password(None, MASKED_PASSWORD, "x") is None
    assert swap_connection_string_password(PG, "", "x") == PG


# ---------------------------------------------------------------------------
# Full round-trip: store -> mask for browser -> echo back -> restore -> resolve
# ---------------------------------------------------------------------------

def test_round_trip_mask_then_restore_then_resolve(monkeypatch):
    # stored at rest: reference inside the string (post-fix state)
    stored = swap_connection_string_password(PG, "s3cret!", REF)

    # browser receives the masked form (the leak fix)
    to_browser = mask_connection_string_password(stored)
    assert "LOCAL_SECRET" not in to_browser and "s3cret!" not in to_browser

    # browser echoes it back on save; server restores from the password column
    restored = swap_connection_string_password(to_browser, MASKED_PASSWORD, REF)
    assert restored == stored

    # runtime resolves the reference to the real password
    import connection_secrets as cs
    monkeypatch.setattr(cs, "get_local_secret", lambda name, default='': "s3cret!" if name == "CONN_PWD_154" else default)
    resolved = cs.resolve_connection_string_secrets(restored)
    assert "Pwd=s3cret!;" in resolved
    assert "LOCAL_SECRET" not in resolved
