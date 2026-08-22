"""Static checks for migrations/019_platform_usage_log_covering_index.sql.

019 rebuilds IX_PlatformUsageLog_RequestTimestamp as a covering index
((RequestTimestamp) INCLUDE (TokensUsed, RequestId, TenantId)) so the monthly
request count in admin_tier_usage.get_agent_user_env_info() and the relay's quota
check stop doing one key lookup per row on the IO-governed S1 tier.

These tests never execute SQL; they pin the properties that make the migration
safe to run on a live database: the exact index shape, ONLINE build, guards, and
no destructive statements.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests_v2.migrations.test_migration_safety import (  # type: ignore
    MIGRATIONS_DIR,
    _read,
    _strip_comments,
)

pytestmark = pytest.mark.migration

PATH = MIGRATIONS_DIR / "019_platform_usage_log_covering_index.sql"

CREATE_INDEX_RE = re.compile(
    r"CREATE\s+NONCLUSTERED\s+INDEX\s+(?P<name>\w+)\s+ON\s+\[dbo\]\.\[PlatformUsageLog\]\s*"
    r"\((?P<key>[^)]*)\)\s*INCLUDE\s*\((?P<include>[^)]*)\)\s*WITH\s*\((?P<with>[^)]*)\)",
    re.IGNORECASE,
)


def _sql() -> str:
    assert PATH.is_file(), f"missing {PATH}"
    return _strip_comments(_read(PATH))


def test_019_exists_and_follows_the_numbering():
    assert PATH.is_file()
    assert (MIGRATIONS_DIR / "018_document_records_delete_cascade.sql").is_file(), \
        "019 follows 018"


def test_019_only_touches_the_expected_index_with_the_covering_shape():
    sql = _sql()
    creates = list(CREATE_INDEX_RE.finditer(sql))
    assert len(creates) == 2, "one rebuild (DROP_EXISTING) path + one create-if-missing path"
    for m in creates:
        assert m.group("name") == "IX_PlatformUsageLog_RequestTimestamp"
        key = [c.strip() for c in m.group("key").split(",")]
        assert key == ["RequestTimestamp"], (
            "key must lead on RequestTimestamp only - the RLS tenant predicate is not "
            "a seek predicate, so TenantId-leading would degrade to a full index scan")
        include = sorted(c.strip() for c in m.group("include").split(","))
        assert include == ["RequestId", "TenantId", "TokensUsed"]
        assert re.search(r"ONLINE\s*=\s*ON", m.group("with"), re.IGNORECASE), \
            "must build ONLINE so the relay's INSERTs keep flowing"
    withs = [m.group("with").upper() for m in creates]
    assert any("DROP_EXISTING" in w for w in withs), "rebuild-in-place path present"
    assert any("DROP_EXISTING" not in w for w in withs), "create-if-missing path present"


def test_019_is_guarded_and_idempotent():
    sql = _sql()
    assert re.search(r"OBJECT_ID\s*\(\s*N'\[dbo\]\.\[PlatformUsageLog\]'", sql)
    assert "sys.indexes" in sql and "sys.index_columns" in sql
    assert "is_included_column = 1" in sql, "skip check must look at the INCLUDE columns"
    assert re.search(r"already covers", sql, re.IGNORECASE), "explicit no-op branch"


def test_019_has_no_destructive_statements():
    sql = _sql()
    for forbidden in (r"\bDROP\s+TABLE\b", r"\bTRUNCATE\b", r"\bDELETE\b", r"\bDROP\s+INDEX\b",
                      r"\bALTER\s+TABLE\b", r"\bUPDATE\b"):
        assert not re.search(forbidden, sql, re.IGNORECASE), forbidden


def test_019_header_documents_rollback_and_ddl_login():
    text = _read(PATH)
    assert "ROLLBACK" in text
    assert "DDL-capable login" in text
    assert "TenantAppUser" in text
    assert "docs/doc-api-concurrency-and-fast-busy.md" in text


def test_019_is_plain_ascii_for_sqlcmd():
    raw = PATH.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    non_ascii = [b for b in raw if b > 127]
    assert not non_ascii, "keep the migration ASCII-only so sqlcmd code pages cannot mangle it"
