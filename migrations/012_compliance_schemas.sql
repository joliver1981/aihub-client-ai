-- ============================================================================
-- Retailer Compliance — Reusable extraction schemas
-- Replaces the per-set inline `extraction_schema` JSON column with a proper
-- ComplianceSchemas table that document sets reference via FK. Schemas can
-- now be defined once in the UI and reused across many sets.
--
-- Priority chain at extraction time:
--   extraction_workflow_id > extraction_schema_id > YAML default fallback
--
-- Idempotent — safe to run multiple times.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. ComplianceSchemas — reusable extraction field definitions
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.objects
               WHERE object_id = OBJECT_ID(N'[dbo].[ComplianceSchemas]') AND type = 'U')
BEGIN
    CREATE TABLE [dbo].[ComplianceSchemas] (
        schema_id       INT             IDENTITY(1,1) PRIMARY KEY,
        TenantId        INT             NOT NULL DEFAULT (CONVERT([int], session_context(N'TenantId'))),
        name            NVARCHAR(255)   NOT NULL,
        description     NVARCHAR(MAX)   NULL,
        fields          NVARCHAR(MAX)   NOT NULL,  -- JSON array of AIExtractNode field defs
        created_by      INT             NULL,
        created_at      DATETIME        NOT NULL DEFAULT GETDATE(),
        updated_at      DATETIME        NOT NULL DEFAULT GETDATE()
    );

    CREATE NONCLUSTERED INDEX IX_ComplianceSchemas_Tenant
        ON [dbo].[ComplianceSchemas] (TenantId);

    PRINT 'Created table: ComplianceSchemas';
END
GO

-- ---------------------------------------------------------------------------
-- 2. RetailerDocumentSets.extraction_schema_id — FK to ComplianceSchemas
-- ---------------------------------------------------------------------------
IF NOT EXISTS (
    SELECT * FROM sys.columns
    WHERE object_id = OBJECT_ID('RetailerDocumentSets') AND name = 'extraction_schema_id'
)
BEGIN
    ALTER TABLE [dbo].[RetailerDocumentSets]
        ADD extraction_schema_id INT NULL;

    PRINT 'Added extraction_schema_id column to RetailerDocumentSets';
END
ELSE
BEGIN
    PRINT 'extraction_schema_id column already exists on RetailerDocumentSets';
END
GO

-- ---------------------------------------------------------------------------
-- 3. Drop deprecated inline `extraction_schema` column (added in migration 011,
--    never used in production — superseded by FK).
-- ---------------------------------------------------------------------------
IF EXISTS (
    SELECT * FROM sys.columns
    WHERE object_id = OBJECT_ID('RetailerDocumentSets') AND name = 'extraction_schema'
)
BEGIN
    ALTER TABLE [dbo].[RetailerDocumentSets]
        DROP COLUMN extraction_schema;

    PRINT 'Dropped deprecated extraction_schema column from RetailerDocumentSets';
END
GO
