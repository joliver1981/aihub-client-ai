-- Migration: 003_data_explorer_dashboards.sql
-- Purpose: Add dashboard persistence for Data Explorer v2
-- Safe for existing installations: creates table only if it doesn't exist

-- ============================================================
-- 1. Create llm_Dashboards table
-- ============================================================

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[llm_Dashboards]') AND type = 'U')
BEGIN
    CREATE TABLE [dbo].[llm_Dashboards] (
        id              NVARCHAR(50)    NOT NULL PRIMARY KEY,
        user_id         INT             NULL,
        title           NVARCHAR(200)   NOT NULL DEFAULT 'Untitled Dashboard',
        description     NVARCHAR(500)   NULL,
        layout_json     NVARCHAR(MAX)   NULL,       -- Full dashboard definition (widgets, positions, queries)
        created_at      DATETIME        NOT NULL DEFAULT GETDATE(),
        updated_at      DATETIME        NOT NULL DEFAULT GETDATE()
    );

    -- Index for user lookups (list my dashboards)
    CREATE NONCLUSTERED INDEX IX_llm_Dashboards_user_id
        ON [dbo].[llm_Dashboards] (user_id)
        INCLUDE (id, title, updated_at);

    PRINT 'Created table [dbo].[llm_Dashboards]';
END
ELSE
BEGIN
    PRINT 'Table [dbo].[llm_Dashboards] already exists — skipping.';
END
