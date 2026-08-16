-- ============================================================================
-- Document Records — ON DELETE CASCADE (follow-up to 017)
--
-- PURPOSE
--   017 created [dbo].[DocumentRecords] with a plain FK to [dbo].[Documents].
--   Every other child of Documents cascades on delete, so purge_document simply
--   deletes the Documents row — which meant deleting any document that HAS
--   extracted rows (vendor guides, scorecards, backfilled leases) failed on
--   FK_DocumentRecords_Documents. Found 2026-08-16 during the record-set
--   lifecycle test.
--
--   The application already works around this (purge_document deletes
--   DocumentRecords rows explicitly first, commit 308e7c1) and that guard is
--   harmless to keep. This migration makes the schema correct on its own, so
--   any OTHER path that deletes a Documents row — future code, manual SQL,
--   support scripts — cannot strand or trip over record rows.
--
-- WHAT IT DOES
--   Recreates FK_DocumentRecords_Documents WITH ON DELETE CASCADE.
--   Idempotent: skips if the FK already cascades; finds the constraint by the
--   referencing table + column rather than trusting the name, in case an
--   install carries an auto-generated one.
--
-- REQUIRES a DDL-capable login (ALTER TABLE). The application login
--   (TenantAppUser) has full DML but no ALTER. Run this the same way 016 and
--   017 were run. No data is read or modified — constraint metadata only, so
--   it is safe on a live system (brief schema lock on DocumentRecords).
--
-- Follows the conventions of 017_document_records.sql.
-- ============================================================================

SET NOCOUNT ON;
GO

IF OBJECT_ID(N'[dbo].[DocumentRecords]', 'U') IS NULL
BEGIN
    PRINT '018: [dbo].[DocumentRecords] does not exist (017 not run) — nothing to do.';
END
ELSE
BEGIN
    DECLARE @fk_name SYSNAME, @cascades BIT;

    SELECT TOP 1
        @fk_name  = fk.name,
        @cascades = CASE WHEN fk.delete_referential_action = 1 THEN 1 ELSE 0 END
    FROM sys.foreign_keys fk
    JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
    JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id
                       AND pc.column_id = fkc.parent_column_id
    WHERE fk.parent_object_id     = OBJECT_ID(N'[dbo].[DocumentRecords]')
      AND fk.referenced_object_id = OBJECT_ID(N'[dbo].[Documents]')
      AND pc.name                 = N'document_id';

    IF @fk_name IS NULL
    BEGIN
        PRINT '018: no FK from DocumentRecords(document_id) to Documents found — adding one WITH CASCADE.';
        ALTER TABLE [dbo].[DocumentRecords] WITH CHECK
            ADD CONSTRAINT FK_DocumentRecords_Documents
            FOREIGN KEY (document_id) REFERENCES [dbo].[Documents](document_id)
            ON DELETE CASCADE;
    END
    ELSE IF @cascades = 1
    BEGIN
        PRINT '018: FK already cascades on delete — nothing to do.';
    END
    ELSE
    BEGIN
        PRINT '018: recreating ' + @fk_name + ' WITH ON DELETE CASCADE...';
        DECLARE @sql NVARCHAR(MAX) =
            N'ALTER TABLE [dbo].[DocumentRecords] DROP CONSTRAINT ' + QUOTENAME(@fk_name) + N';';
        EXEC sp_executesql @sql;

        ALTER TABLE [dbo].[DocumentRecords] WITH CHECK
            ADD CONSTRAINT FK_DocumentRecords_Documents
            FOREIGN KEY (document_id) REFERENCES [dbo].[Documents](document_id)
            ON DELETE CASCADE;

        PRINT '018: done — deleting a Documents row now removes its record rows.';
    END
END
GO
