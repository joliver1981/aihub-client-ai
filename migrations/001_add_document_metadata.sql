-- Migration: Add document_metadata column to Documents table
-- Purpose: Store JSON metadata profiles for Excel files (sheet names, columns, stats, etc.)
-- Required for: Excel Live Data Source feature (Phase 1)
-- Date: 2025-02-13
--
-- This is idempotent - safe to run multiple times.

IF NOT EXISTS (
    SELECT * FROM sys.columns
    WHERE object_id = OBJECT_ID('Documents') AND name = 'document_metadata'
)
BEGIN
    ALTER TABLE Documents ADD document_metadata NVARCHAR(MAX) NULL
    PRINT 'Added document_metadata column to Documents table'
END
ELSE
BEGIN
    PRINT 'document_metadata column already exists on Documents table'
END
GO
