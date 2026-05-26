-- ============================================================================
-- Retailer Compliance Module — Database Migration
-- Adds tables for tracking retailer compliance documents, versions,
-- extracted requirements, and comparison results.
--
-- Idempotent — safe to run multiple times.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Retailers
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.objects
               WHERE object_id = OBJECT_ID(N'[dbo].[Retailers]') AND type = 'U')
BEGIN
    CREATE TABLE [dbo].[Retailers] (
        retailer_id     INT             IDENTITY(1,1) PRIMARY KEY,
        TenantId        INT             NOT NULL DEFAULT (CONVERT([int], session_context(N'TenantId'))),
        name            NVARCHAR(255)   NOT NULL,
        notes           NVARCHAR(MAX)   NULL,
        created_by      INT             NULL,
        created_at      DATETIME        NOT NULL DEFAULT GETDATE(),
        updated_at      DATETIME        NOT NULL DEFAULT GETDATE()
    );

    CREATE NONCLUSTERED INDEX IX_Retailers_Tenant
        ON [dbo].[Retailers] (TenantId);

    PRINT 'Created table: Retailers';
END
GO

-- ---------------------------------------------------------------------------
-- 2. RetailerDocumentSets — groups docs by category per retailer
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.objects
               WHERE object_id = OBJECT_ID(N'[dbo].[RetailerDocumentSets]') AND type = 'U')
BEGIN
    CREATE TABLE [dbo].[RetailerDocumentSets] (
        set_id          INT             IDENTITY(1,1) PRIMARY KEY,
        TenantId        INT             NOT NULL DEFAULT (CONVERT([int], session_context(N'TenantId'))),
        retailer_id     INT             NOT NULL,
        category        NVARCHAR(100)   NOT NULL,
        description     NVARCHAR(500)   NULL,
        created_at      DATETIME        NOT NULL DEFAULT GETDATE(),
        updated_at      DATETIME        NOT NULL DEFAULT GETDATE(),

        CONSTRAINT FK_RetailerDocSets_Retailers
            FOREIGN KEY (retailer_id) REFERENCES [dbo].[Retailers](retailer_id) ON DELETE CASCADE,

        CONSTRAINT UQ_RetailerDocSets_retailer_category
            UNIQUE (retailer_id, category)
    );

    CREATE NONCLUSTERED INDEX IX_RetailerDocSets_Tenant
        ON [dbo].[RetailerDocumentSets] (TenantId, retailer_id);

    PRINT 'Created table: RetailerDocumentSets';
END
GO

-- ---------------------------------------------------------------------------
-- 3. RetailerDocumentVersions — links Documents to sets with versioning
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.objects
               WHERE object_id = OBJECT_ID(N'[dbo].[RetailerDocumentVersions]') AND type = 'U')
BEGIN
    CREATE TABLE [dbo].[RetailerDocumentVersions] (
        version_id      INT             IDENTITY(1,1) PRIMARY KEY,
        TenantId        INT             NOT NULL DEFAULT (CONVERT([int], session_context(N'TenantId'))),
        set_id          INT             NOT NULL,
        document_id     VARCHAR(100)    NOT NULL,
        version_number  INT             NOT NULL,
        is_current      BIT             NOT NULL DEFAULT 1,
        change_summary  NVARCHAR(MAX)   NULL,
        uploaded_by     INT             NULL,
        uploaded_at     DATETIME        NOT NULL DEFAULT GETDATE(),

        CONSTRAINT FK_RetailerDocVersions_Sets
            FOREIGN KEY (set_id) REFERENCES [dbo].[RetailerDocumentSets](set_id) ON DELETE CASCADE
    );

    CREATE NONCLUSTERED INDEX IX_RetailerDocVersions_Set
        ON [dbo].[RetailerDocumentVersions] (TenantId, set_id, is_current)
        INCLUDE (version_number, document_id);

    PRINT 'Created table: RetailerDocumentVersions';
END
GO

-- ---------------------------------------------------------------------------
-- 4. ExtractedRequirements — normalized requirements per version
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.objects
               WHERE object_id = OBJECT_ID(N'[dbo].[ExtractedRequirements]') AND type = 'U')
BEGIN
    CREATE TABLE [dbo].[ExtractedRequirements] (
        requirement_id  INT             IDENTITY(1,1) PRIMARY KEY,
        TenantId        INT             NOT NULL DEFAULT (CONVERT([int], session_context(N'TenantId'))),
        version_id      INT             NOT NULL,
        category        NVARCHAR(100)   NOT NULL,
        subcategory     NVARCHAR(100)   NOT NULL,
        requirement_text NVARCHAR(MAX)  NOT NULL,
        specific_value  NVARCHAR(255)   NULL,
        severity        NVARCHAR(50)    NULL,
        source_page     INT             NULL,
        confidence      FLOAT           NULL,

        CONSTRAINT FK_ExtractedReqs_Versions
            FOREIGN KEY (version_id) REFERENCES [dbo].[RetailerDocumentVersions](version_id) ON DELETE CASCADE
    );

    CREATE NONCLUSTERED INDEX IX_ExtractedReqs_Version
        ON [dbo].[ExtractedRequirements] (TenantId, version_id)
        INCLUDE (category, subcategory);

    CREATE NONCLUSTERED INDEX IX_ExtractedReqs_Category
        ON [dbo].[ExtractedRequirements] (TenantId, category, subcategory);

    PRINT 'Created table: ExtractedRequirements';
END
GO

-- ---------------------------------------------------------------------------
-- 5. ComparisonResults — stored comparison output for audit trail
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.objects
               WHERE object_id = OBJECT_ID(N'[dbo].[ComparisonResults]') AND type = 'U')
BEGIN
    CREATE TABLE [dbo].[ComparisonResults] (
        comparison_id   INT             IDENTITY(1,1) PRIMARY KEY,
        TenantId        INT             NOT NULL DEFAULT (CONVERT([int], session_context(N'TenantId'))),
        comparison_type VARCHAR(20)     NOT NULL,
        source_a_id     INT             NOT NULL,
        source_b_id     INT             NOT NULL,
        result_json     NVARCHAR(MAX)   NOT NULL,
        created_by      INT             NULL,
        created_at      DATETIME        NOT NULL DEFAULT GETDATE()
    );

    CREATE NONCLUSTERED INDEX IX_ComparisonResults_Tenant
        ON [dbo].[ComparisonResults] (TenantId, comparison_type);

    PRINT 'Created table: ComparisonResults';
END
GO
