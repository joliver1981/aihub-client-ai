-- ============================================================================
-- Migration 020: MCPServers.available_to_users — admin "publish" switch for
--                My Connections
--
-- PURPOSE
--   Until now a server appeared on every user's My Connections page the moment
--   it was saved with auth_type = 'oauth2' + grant authorization_code — while
--   it was still being configured. There was no staging state, so a half-set-up
--   server was live and failed for everyone who clicked it. This column gives
--   the admin explicit control over WHEN a configured server becomes visible.
--
-- WHAT IT DOES
--   1. Adds available_to_users BIT NOT NULL DEFAULT 0 to dbo.MCPServers.
--      New servers start UNPUBLISHED.
--   2. Backfill: anything currently eligible stays visible (auth_type = 'oauth2'
--      AND enabled = 1), so existing installs keep today's behaviour. The
--      grant type lives in the encrypted credentials table and cannot be
--      filtered here; client_credentials servers never appear on My Connections
--      anyway (the application filters them), so setting the flag on them is
--      harmless.
--
-- APPLICATION BEHAVIOUR WITHOUT THIS MIGRATION
--   Every read of the column is guarded (builder_mcp/agent_integration/
--   mcp_server_visibility.py) and falls back to VISIBLE, so a deployment that
--   has not applied 020 behaves exactly as before. Apply with a login that has
--   ALTER on dbo.MCPServers — the application's own login (TenantAppUser) does
--   not. The app notices the new column within 5 minutes without a restart.
--
-- ROW-LEVEL SECURITY NOTE
--   MCPServers carries a TenantId column. If your tenant-isolation policy
--   filters rows by SESSION_CONTEXT(N'TenantId') for the login you apply this
--   with, run the backfill once per tenant after
--   EXEC tenant.sp_setTenantContext '<that tenant''s license key>' — or apply
--   as a login your policy exempts. Verify with:
--     SELECT server_id, server_name, auth_type, enabled, available_to_users
--     FROM dbo.MCPServers;
--
-- Idempotent. Safe to re-run. Rollback (walks back the column only):
--   IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'[dbo].[MCPServers]')
--              AND name = 'available_to_users')
--   BEGIN
--       ALTER TABLE [dbo].[MCPServers] DROP CONSTRAINT DF_MCPServers_available_to_users;
--       ALTER TABLE [dbo].[MCPServers] DROP COLUMN available_to_users;
--   END
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Column
-- ---------------------------------------------------------------------------
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID(N'[dbo].[MCPServers]')
      AND name = 'available_to_users'
)
BEGIN
    ALTER TABLE [dbo].[MCPServers]
        ADD available_to_users BIT NOT NULL
            CONSTRAINT DF_MCPServers_available_to_users DEFAULT (0);
    PRINT 'MCPServers.available_to_users added (default 0 = unpublished)';
END
ELSE
BEGIN
    PRINT 'MCPServers.available_to_users already present — skipped';
END
GO

-- ---------------------------------------------------------------------------
-- 2. Backfill — preserve today's behaviour for existing installs
-- ---------------------------------------------------------------------------
-- Runs only on the first application (the column did not exist a moment ago,
-- so no row can have been deliberately set to 0 yet). Re-running the file after
-- an admin has unpublished a server must NOT re-publish it, hence the guard on
-- a marker that this backfill has already run: we use the presence of any row
-- with the flag set, which can only come from this backfill or a later admin
-- action — either way the operator has taken over and we leave it alone.
IF EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID(N'[dbo].[MCPServers]')
      AND name = 'available_to_users'
)
AND NOT EXISTS (SELECT 1 FROM [dbo].[MCPServers] WHERE available_to_users = 1)
BEGIN
    UPDATE [dbo].[MCPServers]
       SET available_to_users = 1
     WHERE auth_type = 'oauth2'
       AND enabled = 1;
    PRINT CONCAT('Backfill: ', @@ROWCOUNT, ' enabled OAuth server(s) kept visible to users');
END
GO
