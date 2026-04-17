-- Feedback System Schema Migration
-- Run this script against the database BEFORE deploying the updated application.
-- All changes are non-breaking: new columns are nullable or have defaults.
-- Existing rows will get status = 'new'.

-- 1. Add SQL query storage column
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('[dbo].[ai_feedback]') AND name = 'sql_query')
BEGIN
    ALTER TABLE [dbo].[ai_feedback] ADD [sql_query] NVARCHAR(MAX) NULL;
    PRINT 'Added column: sql_query';
END
ELSE
    PRINT 'Column sql_query already exists';
GO

-- 2. Add admin workflow status column
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('[dbo].[ai_feedback]') AND name = 'status')
BEGIN
    ALTER TABLE [dbo].[ai_feedback] ADD [status] VARCHAR(20) NOT NULL DEFAULT 'new';
    PRINT 'Added column: status';
END
ELSE
    PRINT 'Column status already exists';
GO

-- 3. Add admin notes column
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('[dbo].[ai_feedback]') AND name = 'admin_notes')
BEGIN
    ALTER TABLE [dbo].[ai_feedback] ADD [admin_notes] NVARCHAR(MAX) NULL;
    PRINT 'Added column: admin_notes';
END
ELSE
    PRINT 'Column admin_notes already exists';
GO

-- 4. Add reviewed_by column (references User table)
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('[dbo].[ai_feedback]') AND name = 'reviewed_by')
BEGIN
    ALTER TABLE [dbo].[ai_feedback] ADD [reviewed_by] INT NULL;
    PRINT 'Added column: reviewed_by';
END
ELSE
    PRINT 'Column reviewed_by already exists';
GO

-- 5. Add reviewed_at timestamp column
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('[dbo].[ai_feedback]') AND name = 'reviewed_at')
BEGIN
    ALTER TABLE [dbo].[ai_feedback] ADD [reviewed_at] DATETIME NULL;
    PRINT 'Added column: reviewed_at';
END
ELSE
    PRINT 'Column reviewed_at already exists';
GO

-- 6. Create indexes for dashboard query performance
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('[dbo].[ai_feedback]') AND name = 'IX_ai_feedback_status')
BEGIN
    CREATE INDEX IX_ai_feedback_status ON [dbo].[ai_feedback] ([status]);
    PRINT 'Created index: IX_ai_feedback_status';
END
ELSE
    PRINT 'Index IX_ai_feedback_status already exists';
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('[dbo].[ai_feedback]') AND name = 'IX_ai_feedback_user_id')
BEGIN
    CREATE INDEX IX_ai_feedback_user_id ON [dbo].[ai_feedback] ([user_id]);
    PRINT 'Created index: IX_ai_feedback_user_id';
END
ELSE
    PRINT 'Index IX_ai_feedback_user_id already exists';
GO

PRINT 'Feedback schema migration complete.';
