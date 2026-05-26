-- ============================================================================
-- Per-Agent Document-Type Allow List — Database Migration
-- Lets an admin restrict a custom agent's documents tool to specific
-- document types (security / data isolation).
--
-- Semantic: rows in this table form an ALLOW LIST.
--   - If an agent has zero rows → unrestricted (current behavior, no change).
--   - If an agent has one or more rows → its document tools are filtered to
--     only those document_type values, server-side.
--
-- Mirrors the [dbo].[AgentTools] junction-table convention and the
-- TenantId / session_context default used by the cc_* tables in
-- migration 004_command_center.sql.
-- ============================================================================

IF NOT EXISTS (SELECT 1 FROM sys.objects
               WHERE object_id = OBJECT_ID(N'[dbo].[AgentDocumentTypes]') AND type = 'U')
BEGIN
    CREATE TABLE [dbo].[AgentDocumentTypes] (
        id              INT          IDENTITY(1,1) PRIMARY KEY,
        TenantId        INT          NOT NULL DEFAULT (CONVERT([int], session_context(N'TenantId'))),
        agent_id        INT          NOT NULL,
        document_type   VARCHAR(100) NOT NULL,  -- matches Documents.document_type length
        create_date     DATETIME     NOT NULL DEFAULT GETDATE(),

        CONSTRAINT FK_AgentDocumentTypes_Agents
            FOREIGN KEY (agent_id) REFERENCES [dbo].[Agents](id) ON DELETE CASCADE,

        -- Same (agent, type) twice would be a no-op; reject at the DB so
        -- bad client code can't pollute the table.
        CONSTRAINT UQ_AgentDocumentTypes_agent_type UNIQUE (agent_id, document_type)
    );

    -- Read pattern: WHERE TenantId = @t AND agent_id = @a → list document_types
    CREATE NONCLUSTERED INDEX IX_AgentDocumentTypes_agent
        ON [dbo].[AgentDocumentTypes] (TenantId, agent_id)
        INCLUDE (document_type);

    PRINT 'Created table: AgentDocumentTypes';
END
GO
