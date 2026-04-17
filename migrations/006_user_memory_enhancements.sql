-- Migration 006: Formalize cc_UserMemory tracking columns
-- These columns are already referenced by the application code but may not
-- exist on every deployment.  The IF NOT EXISTS guards make this migration
-- safe to re-run (idempotent).

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('cc_UserMemory') AND name = 'success_count'
)
    ALTER TABLE cc_UserMemory ADD success_count INT NOT NULL DEFAULT 0;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('cc_UserMemory') AND name = 'fail_count'
)
    ALTER TABLE cc_UserMemory ADD fail_count INT NOT NULL DEFAULT 0;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('cc_UserMemory') AND name = 'smart_label'
)
    ALTER TABLE cc_UserMemory ADD smart_label NVARCHAR(200) NULL;
GO
