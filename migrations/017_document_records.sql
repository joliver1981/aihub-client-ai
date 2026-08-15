-- ============================================================================
-- Document Records — Database Migration (multi-shape extraction)
--
-- PURPOSE
--   A document has two kinds of extractable content:
--     FIELDS  — one value per document (title, tenant, effective date, payment terms).
--               Already stored in [dbo].[DocumentFields]. Unchanged by this migration.
--     RECORDS — many of a thing, each with attributes that belong together
--               (a manual's requirements, an invoice's line items, a lease's rent steps).
--               There has been nowhere to put these, so extraction dropped them: a
--               108-page vendor guide's ~112 requirements were smeared across a flat
--               field namespace as fob_points[0].rates.OPO.rate_20ft and similar.
--   This table is the missing home.
--
-- SHAPE — one row per RECORD, not per cell.
--   Cell-level EAV would need an N-way self-join to reassemble one requirement and
--   would force provenance to be per-cell. A JSON blob per document cannot be indexed
--   or counted across documents. A table per record type would need DDL every time a
--   client ingests a new kind of document — which defeats the point of a general system.
--   So: a typed spine that every record type shares + row_json for the columns that
--   vary by type.
--
-- THE __manifest ROW — do not remove it.
--   One row per document per extraction run with record_set = '__manifest' records what
--   was ATTEMPTED. Without it, "this guide has no EDI requirement" and "we never ran
--   record extraction on this guide" are indistinguishable, and the second one silently
--   answers as though it were the first. That is the difference between an auditable
--   count and a confidently wrong one.
--
-- REQUIRES a DDL-capable login. The application login (TenantAppUser) has full DML but
--   no CREATE TABLE / ALTER. Run this the same way 008 and 016 were run.
--
-- The application degrades gracefully when this table is absent: record extraction is
--   skipped with a log line, and field extraction is unaffected. Nothing breaks if this
--   migration is never run — records simply do not accumulate.
--
-- Follows the conventions of 016_document_categories_and_group_access.sql.
-- ============================================================================

SET NOCOUNT ON;
GO

IF NOT EXISTS (SELECT 1 FROM sys.objects
               WHERE object_id = OBJECT_ID(N'[dbo].[DocumentRecords]') AND type = 'U')
BEGIN
    CREATE TABLE [dbo].[DocumentRecords] (
        record_id     BIGINT        IDENTITY(1,1) PRIMARY KEY,
        TenantId      INT           NOT NULL DEFAULT (CONVERT([int], session_context(N'TenantId'))),
        document_id   VARCHAR(100)  NOT NULL,
        record_set    VARCHAR(64)   NOT NULL,   -- e.g. 'requirements'; '__manifest' = the run ledger
        row_index     INT           NOT NULL,   -- 0-based position within the set

        -- The type-specific columns, as declared by the document type's schema.
        row_json      NVARCHAR(MAX) NOT NULL,

        -- Provenance. Non-negotiable: a record nobody can trace back to a page and a
        -- verbatim sentence cannot be audited, and an unauditable count is not an answer.
        source_pages  VARCHAR(100)  NULL,       -- e.g. '6' or '8,9'
        excerpt       NVARCHAR(2000) NULL,      -- verbatim sentence the row came from
        confidence    FLOAT         NULL,

        extractor_model VARCHAR(100) NULL,
        created_at    DATETIME      NOT NULL DEFAULT GETDATE(),

        CONSTRAINT FK_DocumentRecords_Documents
            FOREIGN KEY (document_id) REFERENCES [dbo].[Documents](document_id),

        -- Re-extracting a document replaces its rows; the same slot may not exist twice.
        CONSTRAINT UQ_DocumentRecords_slot UNIQUE (document_id, record_set, row_index)
    );

    -- Read pattern 1: every row of a set across all documents ("which guides require X").
    CREATE NONCLUSTERED INDEX IX_DocumentRecords_set
        ON [dbo].[DocumentRecords] (TenantId, record_set, document_id)
        INCLUDE (row_index, source_pages, confidence);

    -- Read pattern 2: everything extracted from one document.
    CREATE NONCLUSTERED INDEX IX_DocumentRecords_doc
        ON [dbo].[DocumentRecords] (TenantId, document_id, record_set);

    PRINT 'Created table: DocumentRecords';
END
GO

-- ============================================================================
-- VERIFY / EXAMPLE QUERIES
-- ============================================================================
/*
-- What record sets exist, and how much of each?
SELECT record_set, COUNT(*) AS rows_stored, COUNT(DISTINCT document_id) AS documents
FROM   [dbo].[DocumentRecords]
WHERE  record_set <> '__manifest'
GROUP  BY record_set;

-- Coverage frame: which documents were ATTEMPTED, and what did they yield?
-- A document missing from this result was never run — that is NOT the same as zero rows.
SELECT m.document_id,
       JSON_VALUE(m.row_json, '$.record_set')     AS attempted_set,
       JSON_VALUE(m.row_json, '$.rows_written')   AS rows_written,
       JSON_VALUE(m.row_json, '$.pages_total')    AS pages_total,
       JSON_VALUE(m.row_json, '$.status')         AS status
FROM   [dbo].[DocumentRecords] m
WHERE  m.record_set = '__manifest';

-- "Which of our retailer guides require an 856 ASN?"  (rows, with citations)
SELECT d.filename,
       JSON_VALUE(r.row_json, '$.topic')       AS topic,
       JSON_VALUE(r.row_json, '$.requirement') AS requirement,
       JSON_VALUE(r.row_json, '$.value')       AS value,
       r.source_pages, r.excerpt
FROM   [dbo].[DocumentRecords] r
JOIN   [dbo].[Documents] d ON d.document_id = r.document_id
WHERE  r.record_set = 'requirements'
  AND (r.row_json LIKE '%856%' OR r.excerpt LIKE '%856%')
ORDER  BY d.filename, r.row_index;
*/

-- ============================================================================
-- ROLLBACK
-- ============================================================================
/*
DROP TABLE IF EXISTS [dbo].[DocumentRecords];
*/
