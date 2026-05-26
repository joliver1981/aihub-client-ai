-- ============================================================================
-- Retailer Compliance — Per-set extraction configuration
-- Adds two optional columns to RetailerDocumentSets:
--   * extraction_schema       NVARCHAR(MAX) — JSON array of custom field defs
--   * extraction_workflow_id  INT           — FK to a workflow that handles extraction
--
-- Priority chain at extraction time:
--   extraction_workflow_id > extraction_schema > default YAML taxonomy
--
-- Idempotent — safe to run multiple times.
-- ============================================================================

IF NOT EXISTS (
    SELECT * FROM sys.columns
    WHERE object_id = OBJECT_ID('RetailerDocumentSets') AND name = 'extraction_schema'
)
BEGIN
    ALTER TABLE [dbo].[RetailerDocumentSets]
        ADD extraction_schema NVARCHAR(MAX) NULL;

    PRINT 'Added extraction_schema column to RetailerDocumentSets';
END
ELSE
BEGIN
    PRINT 'extraction_schema column already exists on RetailerDocumentSets';
END
GO

IF NOT EXISTS (
    SELECT * FROM sys.columns
    WHERE object_id = OBJECT_ID('RetailerDocumentSets') AND name = 'extraction_workflow_id'
)
BEGIN
    ALTER TABLE [dbo].[RetailerDocumentSets]
        ADD extraction_workflow_id INT NULL;

    PRINT 'Added extraction_workflow_id column to RetailerDocumentSets';
END
ELSE
BEGIN
    PRINT 'extraction_workflow_id column already exists on RetailerDocumentSets';
END
GO
