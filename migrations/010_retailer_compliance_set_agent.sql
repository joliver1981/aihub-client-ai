-- ============================================================================
-- Retailer Compliance — Add agent_id to document sets
-- Associates each document set with a compliance agent so all uploads to that
-- set automatically get indexed into the agent's knowledge base.
--
-- Idempotent — safe to run multiple times.
-- ============================================================================

IF NOT EXISTS (
    SELECT * FROM sys.columns
    WHERE object_id = OBJECT_ID('RetailerDocumentSets') AND name = 'agent_id'
)
BEGIN
    ALTER TABLE [dbo].[RetailerDocumentSets]
        ADD agent_id INT NULL;

    PRINT 'Added agent_id column to RetailerDocumentSets';
END
ELSE
BEGIN
    PRINT 'agent_id column already exists on RetailerDocumentSets';
END
GO
