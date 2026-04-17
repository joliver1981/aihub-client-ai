-- ============================================================================
-- Command Center Agent — Database Migration
-- Creates tables for user memory, tool audit, plugins, and sessions.
-- All tables include TenantId with RLS support.
-- ============================================================================

-- Table 1: Per-user interaction patterns & preferences
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[cc_UserMemory]') AND type = 'U')
BEGIN
    CREATE TABLE [dbo].[cc_UserMemory] (
        id              INT IDENTITY(1,1) PRIMARY KEY,
        TenantId        INT             NOT NULL DEFAULT (CONVERT([int], session_context(N'TenantId'))),
        user_id         INT             NOT NULL,
        memory_type     NVARCHAR(50)    NOT NULL,  -- 'preference', 'pattern', 'context', 'faq'
        memory_key      NVARCHAR(200)   NOT NULL,
        memory_value    NVARCHAR(MAX)   NOT NULL,  -- JSON blob
        usage_count     INT             NOT NULL DEFAULT 1,
        last_used       DATETIME        NOT NULL DEFAULT GETDATE(),
        created_at      DATETIME        NOT NULL DEFAULT GETDATE(),
        updated_at      DATETIME        NOT NULL DEFAULT GETDATE()
    );

    CREATE NONCLUSTERED INDEX IX_cc_UserMemory_user
        ON [dbo].[cc_UserMemory] (TenantId, user_id, memory_type)
        INCLUDE (memory_key, usage_count, last_used);

    PRINT 'Created table: cc_UserMemory';
END
GO

-- Table 2: Auto-generated tool tracking
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[cc_ToolAudit]') AND type = 'U')
BEGIN
    CREATE TABLE [dbo].[cc_ToolAudit] (
        id              INT IDENTITY(1,1) PRIMARY KEY,
        TenantId        INT             NOT NULL DEFAULT (CONVERT([int], session_context(N'TenantId'))),
        tool_name       NVARCHAR(200)   NOT NULL,
        created_by      INT             NULL,       -- user_id or NULL for system
        creation_method NVARCHAR(50)    NOT NULL,   -- 'auto', 'manual', 'plugin'
        config_json     NVARCHAR(MAX)   NULL,       -- config.json snapshot
        code_hash       NVARCHAR(64)    NULL,       -- SHA-256 of code.py
        usage_count     INT             NOT NULL DEFAULT 0,
        last_used       DATETIME        NULL,
        status          NVARCHAR(20)    NOT NULL DEFAULT 'active', -- active, disabled, deleted
        created_at      DATETIME        NOT NULL DEFAULT GETDATE()
    );

    CREATE NONCLUSTERED INDEX IX_cc_ToolAudit_tenant
        ON [dbo].[cc_ToolAudit] (TenantId, status)
        INCLUDE (tool_name, usage_count);

    PRINT 'Created table: cc_ToolAudit';
END
GO

-- Table 3: Plugin registry
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[cc_Plugins]') AND type = 'U')
BEGIN
    CREATE TABLE [dbo].[cc_Plugins] (
        id              INT IDENTITY(1,1) PRIMARY KEY,
        TenantId        INT             NOT NULL DEFAULT (CONVERT([int], session_context(N'TenantId'))),
        plugin_id       NVARCHAR(100)   NOT NULL,
        display_name    NVARCHAR(200)   NOT NULL,
        version         NVARCHAR(20)    NOT NULL DEFAULT '1.0.0',
        description     NVARCHAR(500)   NULL,
        manifest_json   NVARCHAR(MAX)   NULL,
        enabled         BIT             NOT NULL DEFAULT 1,
        installed_at    DATETIME        NOT NULL DEFAULT GETDATE()
    );

    CREATE UNIQUE NONCLUSTERED INDEX IX_cc_Plugins_unique
        ON [dbo].[cc_Plugins] (TenantId, plugin_id);

    PRINT 'Created table: cc_Plugins';
END
GO

-- Table 4: Chat sessions (DB-backed for RLS)
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[cc_Sessions]') AND type = 'U')
BEGIN
    CREATE TABLE [dbo].[cc_Sessions] (
        session_id      NVARCHAR(50)    NOT NULL PRIMARY KEY,
        TenantId        INT             NOT NULL DEFAULT (CONVERT([int], session_context(N'TenantId'))),
        user_id         INT             NOT NULL,
        title           NVARCHAR(200)   NOT NULL DEFAULT 'New Chat',
        messages_json   NVARCHAR(MAX)   NULL,     -- Full message history
        state_json      NVARCHAR(MAX)   NULL,     -- Serialized graph state snapshot
        created_at      DATETIME        NOT NULL DEFAULT GETDATE(),
        updated_at      DATETIME        NOT NULL DEFAULT GETDATE()
    );

    CREATE NONCLUSTERED INDEX IX_cc_Sessions_user
        ON [dbo].[cc_Sessions] (TenantId, user_id)
        INCLUDE (session_id, title, updated_at);

    PRINT 'Created table: cc_Sessions';
END
GO

PRINT 'Command Center migration complete.';
GO
