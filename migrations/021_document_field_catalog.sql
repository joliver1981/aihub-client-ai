-- ============================================================================
-- Migration 021: DocumentFieldCatalog — per-type catalog of extracted fields
--
-- PURPOSE
--   The document-search page offered every distinct extracted field name in
--   one dropdown (8,791 names on a 397-document store; 5,276 of them occur in
--   exactly one document) and GROUP BY'd DocumentFields on every page render.
--   This table answers "which fields does document type X carry, and how many
--   documents carry each" from a small indexed read. It feeds the page's
--   per-type field type-ahead (document_field_catalog.suggest).
--
-- WHAT IT DOES
--   Creates dbo.DocumentFieldCatalog (one row per document_type + field_path)
--   with a unique index on (document_type, SHA1(field_path)) — the hash keeps
--   the key under the 900-byte limit of older SQL Server versions — and a
--   (document_type, doc_count DESC) index for ranked reads.
--
-- MAINTENANCE
--   Rows are kept current at ingest (LLMDocumentEngine._store_in_sql_db ->
--   document_field_catalog.record_document, exact per-document recount, so a
--   re-ingest never double-counts). Build the initial catalog from existing
--   data with:   python run_document_field_catalog_backfill.py
--   or the admin "Rebuild field catalog" action on the search page.
--
-- APPLICATION BEHAVIOUR WITHOUT THIS MIGRATION
--   The application tries to create the table itself on first use; where the
--   app login lacks CREATE TABLE it falls back to computing the same answer
--   live from DocumentFields for the requested type(s) only (cached 5 min)
--   and logs a pointer to the backfill. Nothing breaks; suggestions are just
--   slower on large stores until this is applied.
-- ============================================================================

IF OBJECT_ID('dbo.DocumentFieldCatalog', 'U') IS NULL
CREATE TABLE dbo.DocumentFieldCatalog (
    catalog_id      INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    TenantId        INT NULL,
    document_type   VARCHAR(100)  NOT NULL,
    field_name      NVARCHAR(255) NOT NULL,
    field_path      NVARCHAR(500) NOT NULL,
    field_path_hash AS CONVERT(BINARY(20), HASHBYTES('SHA1', field_path)) PERSISTED,
    doc_count       INT NOT NULL CONSTRAINT DF_DocumentFieldCatalog_doc_count DEFAULT 0,
    row_count       INT NOT NULL CONSTRAINT DF_DocumentFieldCatalog_row_count DEFAULT 0,
    first_seen      DATETIME NOT NULL CONSTRAINT DF_DocumentFieldCatalog_first_seen DEFAULT GETUTCDATE(),
    last_seen       DATETIME NOT NULL CONSTRAINT DF_DocumentFieldCatalog_last_seen DEFAULT GETUTCDATE()
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_DocumentFieldCatalog_type_path')
CREATE UNIQUE INDEX UX_DocumentFieldCatalog_type_path
    ON dbo.DocumentFieldCatalog (document_type, field_path_hash);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_DocumentFieldCatalog_type_count')
CREATE INDEX IX_DocumentFieldCatalog_type_count
    ON dbo.DocumentFieldCatalog (document_type, doc_count DESC);
GO
