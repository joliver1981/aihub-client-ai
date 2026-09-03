"""document_field_catalog — the per-type field catalog behind the document
search page's type-ahead (2026-09-03). Pure logic over a recording fake
cursor; no database.

Invariants:
  * suggest() ranks schema-declared fields first, then by document count,
    drops names seen in fewer than MIN_DOCS documents unless the query names
    one exactly, and honours the limit;
  * a missing table (migration 021 not applied) or an empty catalog for the
    requested types falls back to a LIVE read of DocumentFields bounded to
    those types — never the whole store — and caches it;
  * record_document() recomputes exact counts for the paths ONE document
    carries (chunked IN lists, UPDATE then INSERT) and invalidates the cache;
  * rebuild() replaces the rows for the requested types (or all) from the
    same observed query.
"""
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import document_field_catalog as dfc  # noqa: E402

pytestmark = pytest.mark.unit


class FakeCursor:
    """Answers by SQL shape; records every statement."""

    def __init__(self, table_exists=True, catalog_rows=(), observed_rows=(),
                 doc_paths=(), update_hits=True):
        self.calls = []
        self.table = table_exists
        self.catalog_rows = list(catalog_rows)     # (field_name, field_path, doc_count)
        self.observed_rows = list(observed_rows)   # (type, name, path, doc_count, row_count)
        self.doc_paths = list(doc_paths)
        self.update_hits = update_hits
        self.rowcount = 0
        self._last = ""

    def execute(self, sql, *params):
        self._last = " ".join(sql.split())
        self.calls.append((self._last, params))
        if self._last.startswith("UPDATE"):
            self.rowcount = 1 if self.update_hits else 0
        elif self._last.startswith("INSERT"):
            self.rowcount = 1

    def executemany(self, sql, rows):
        self.calls.append((" ".join(sql.split()), tuple(rows)))

    def fetchone(self):
        if "OBJECT_ID" in self._last:
            return (7,) if self.table else (None,)
        if "SELECT document_type FROM Documents" in self._last:
            return ("lease_agreement",)
        if "COUNT(*), COUNT(DISTINCT document_type)" in self._last:
            return (len(self.catalog_rows), 1, None)
        return None

    def fetchall(self):
        if "FROM dbo.DocumentFieldCatalog" in self._last:
            return list(self.catalog_rows)
        if "FROM DocumentFields f JOIN DocumentPages p ON p.page_id = f.page_id WHERE p.document_id" in self._last:
            return [(p,) for p in self.doc_paths]
        if "FROM DocumentFields f" in self._last:
            return list(self.observed_rows)
        return []


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    dfc.invalidate(None)
    dfc._table_state.update(exists=None, checked=0.0)
    dfc._fallback_logged.clear()
    dfc._schema_cache.clear()
    monkeypatch.setattr(dfc, "SCHEMA_DIR", str(tmp_path))     # no curated schemas unless a test adds one
    monkeypatch.setattr(dfc, "MIN_DOCS", 2)


def _schema(tmp_path, doc_type, fields):
    (tmp_path / f"{doc_type}_auto.yml").write_text(
        "document_type: %s\nfields:\n%s" % (doc_type, "".join(f"  {f}:\n    description: x\n" for f in fields)),
        encoding="utf-8")


# ---------------------------------------------------------------- naming
def test_display_name_groups_and_strips_list_indexes():
    assert dfc.display_name("financial_terms.base_rent_amount") == "Financial Terms › Base Rent Amount"
    assert dfc.display_name("line_items[3].unit_price") == "Line Items › Unit Price"
    assert dfc.display_name("agreement_date") == "Agreement Date"
    assert dfc.display_name("", "vendor") == "Vendor"


# ---------------------------------------------------------------- suggest
CATALOG = [("base_rent_amount", "financial_terms.base_rent_amount", 150),
           ("commencement_date", "lease_term.commencement_date", 140),
           ("weird_private_name", "weird_private_name", 1),
           ("hvac_responsible_party", "article_5.hvac_responsible_party", 40),
           ("tenant", "parties.tenant", 180)]


def test_suggest_ranks_schema_first_then_by_doc_count_and_drops_singletons(tmp_path):
    _schema(tmp_path, "lease_agreement", ["article_5.hvac_responsible_party"])
    cur = FakeCursor(catalog_rows=CATALOG)
    out = dfc.suggest(cur, ["lease_agreement"], "", limit=50)
    paths = [r["path"] for r in out]
    assert paths[0] == "article_5.hvac_responsible_party" and out[0]["in_schema"] is True
    assert paths[1:] == ["parties.tenant", "financial_terms.base_rent_amount",
                         "lease_term.commencement_date"]
    assert "weird_private_name" not in paths, "a field seen in ONE document is noise"
    assert out[1]["display_name"] == "Parties › Tenant" and out[1]["doc_count"] == 180


def test_suggest_query_filters_and_exact_match_keeps_a_singleton():
    cur = FakeCursor(catalog_rows=CATALOG)
    assert [r["path"] for r in dfc.suggest(cur, ["lease_agreement"], "rent")] == \
        ["financial_terms.base_rent_amount"]
    assert [r["path"] for r in dfc.suggest(cur, ["lease_agreement"], "Commencement")] == \
        ["lease_term.commencement_date"]                       # display-name match, case-insensitive
    assert [r["path"] for r in dfc.suggest(cur, ["lease_agreement"], "weird_private_name")] == \
        ["weird_private_name"]                                 # exact name match beats MIN_DOCS
    assert dfc.suggest(cur, ["lease_agreement"], "zzz-nothing") == []


def test_suggest_limit_and_empty_types():
    cur = FakeCursor(catalog_rows=CATALOG)
    assert len(dfc.suggest(cur, ["lease_agreement"], "", limit=2)) == 2
    assert dfc.suggest(cur, [], "") == []
    assert dfc.suggest(cur, ["", None], "") == []


def test_suggest_reads_the_catalog_scoped_to_the_types_and_caches():
    cur = FakeCursor(catalog_rows=CATALOG)
    dfc.suggest(cur, ["lease_agreement", "lease_amendment"], "")
    sql, params = next(c for c in cur.calls if "FROM dbo.DocumentFieldCatalog" in c[0])
    assert "document_type IN (?,?)" in sql and params == ("lease_agreement", "lease_amendment")
    n = len(cur.calls)
    dfc.suggest(cur, ["lease_amendment", "lease_agreement"], "")   # same set, other order
    assert len(cur.calls) == n, "served from cache"
    dfc.invalidate("lease_agreement")
    dfc.suggest(cur, ["lease_agreement", "lease_amendment"], "")
    assert len(cur.calls) > n, "invalidate() drops the cached read"


def test_missing_table_falls_back_to_a_bounded_live_read(caplog):
    cur = FakeCursor(table_exists=False,
                     observed_rows=[("vendor_guide", "carton", "packaging.carton", 6, 40),
                                    ("vendor_guide", "one_off", "one_off", 1, 1)])
    out = dfc.suggest(cur, ["vendor_guide"], "")
    assert [r["path"] for r in out] == ["packaging.carton"]
    live = [c for c in cur.calls if "FROM DocumentFields f" in c[0]]
    assert live and "d.document_type IN (?)" in live[0][0] and live[0][1] == ("vendor_guide",)
    assert not any("FROM dbo.DocumentFieldCatalog" in c[0] for c in cur.calls)
    assert "run_document_field_catalog_backfill" in caplog.text


def test_empty_catalog_for_a_type_also_falls_back():
    cur = FakeCursor(table_exists=True, catalog_rows=[],
                     observed_rows=[("resume", "skills", "skills", 30, 90)])
    assert [r["path"] for r in dfc.suggest(cur, ["resume"], "")] == ["skills"]


# ---------------------------------------------------------------- writes
def test_record_document_recounts_its_paths_in_chunks_and_upserts(monkeypatch):
    monkeypatch.setattr(dfc, "CHUNK", 2)
    cur = FakeCursor(doc_paths=["a", "b", "c"], update_hits=False,
                     observed_rows=[("lease_agreement", "a", "a", 3, 9)])
    n = dfc.record_document(cur, "doc-1")           # type looked up from Documents
    observed = [c for c in cur.calls if "COALESCE(f.field_path, f.field_name) IN" in c[0]]
    assert len(observed) == 2, "3 paths / CHUNK 2 -> two bounded observed queries"
    assert observed[0][1] == ("lease_agreement", "a", "b") and observed[1][1] == ("lease_agreement", "c")
    kinds = [c[0].split()[0] for c in cur.calls if c[0].startswith(("UPDATE", "INSERT"))]
    assert kinds == ["UPDATE", "INSERT", "UPDATE", "INSERT"], "update miss -> insert, per observed row"
    assert n == 2


def test_record_document_is_a_noop_without_the_table_and_permissions(monkeypatch):
    cur = FakeCursor(table_exists=False, doc_paths=["a"])
    monkeypatch.setattr(dfc, "ensure_table", lambda c: False)
    assert dfc.record_document(cur, "doc-1", "lease_agreement") == 0
    assert not any(c[0].startswith(("UPDATE", "INSERT")) for c in cur.calls)


def test_rebuild_replaces_rows_for_the_requested_types():
    cur = FakeCursor(observed_rows=[("lease_agreement", "a", "a", 3, 9),
                                    ("lease_agreement", "b", "b", 2, 2),
                                    ("resume", "s", "s", 30, 90)])
    counts = dfc.rebuild(cur, ["lease_agreement", "resume"])
    delete = next(c for c in cur.calls if c[0].startswith("DELETE"))
    assert "document_type IN (?,?)" in delete[0] and delete[1] == ("lease_agreement", "resume")
    insert = next(c for c in cur.calls if c[0].startswith("INSERT"))
    assert len(insert[1]) == 3
    assert counts == {"lease_agreement": 2, "resume": 1}


def test_rebuild_all_deletes_everything():
    cur = FakeCursor(observed_rows=[("x", "a", "a", 1, 1)])
    dfc.rebuild(cur, None)
    delete = next(c for c in cur.calls if c[0].startswith("DELETE"))
    assert "WHERE" not in delete[0]


def test_schema_fields_reads_the_curated_vocabulary(tmp_path):
    _schema(tmp_path, "lease_agreement", ["lease_term.commencement_date", "parties.tenant"])
    _schema(tmp_path, "resume", ["skills"])
    assert dfc.schema_fields("lease_agreement") == {"lease_term.commencement_date", "parties.tenant"}
    assert dfc.schema_fields("resume") == {"skills"}
    assert dfc.schema_fields("no_such_type") == set()


def test_stats_without_the_table():
    assert dfc.stats(FakeCursor(table_exists=False)) == {"table": False, "rows": 0, "types": 0, "last_seen": None}


def test_stats_with_the_table():
    s = dfc.stats(FakeCursor(catalog_rows=CATALOG))
    assert s["table"] is True and s["rows"] == len(CATALOG)


def test_table_existence_is_cached_briefly():
    cur = FakeCursor(table_exists=True)
    assert dfc.table_exists(cur) is True
    cur.table = False
    assert dfc.table_exists(cur) is True, "cached for TTL seconds"
    assert dfc.table_exists(cur, ttl=0) is False, "ttl=0 forces a re-check"
