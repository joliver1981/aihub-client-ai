"""Run migration 008 — per-agent document-type allow list.

Creates the ``[dbo].[AgentDocumentTypes]`` junction table used by the
agent-builder's "Document Type Restrictions" feature. Idempotent: the
migration is wrapped in ``IF NOT EXISTS``, so re-running is safe.

Mirrors run_cc_migration.py (migration 004); same connection idiom.
"""
import config as cfg
import pyodbc

conn = pyodbc.connect(
    f"DRIVER={{SQL Server}};SERVER={cfg.DATABASE_SERVER};"
    f"DATABASE={cfg.DATABASE_NAME};UID={cfg.DATABASE_UID};PWD={cfg.DATABASE_PWD}"
)
cursor = conn.cursor()

with open("migrations/008_agent_document_type_restrictions.sql", "r") as f:
    migration_sql = f.read()

# Split by GO and execute each batch
batches = migration_sql.split("\nGO")
for batch in batches:
    batch = batch.strip()
    if batch and not batch.startswith("--"):
        try:
            cursor.execute(batch)
            conn.commit()
            while cursor.nextset():
                pass
        except Exception as e:
            print(f"Batch error: {e}")

# Verify the table is present
cursor.execute(
    "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
    "WHERE TABLE_NAME = 'AgentDocumentTypes'"
)
found = [row[0] for row in cursor.fetchall()]
print(f"AgentDocumentTypes table present: {bool(found)}")
conn.close()
print("Migration 008 complete.")
