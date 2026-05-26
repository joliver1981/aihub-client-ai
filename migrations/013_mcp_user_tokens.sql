-- ============================================================================
-- MCP per-user OAuth tokens
--
-- Before this migration:
--   OAuth tokens (oauth_access_token, oauth_refresh_token, oauth_expires_at)
--   lived in MCPServerCredentials alongside the server-level app config. This
--   meant ONE token per MCP server — fine for single-user dev, broken for
--   multi-user installs (User A's authorize would silently overwrite User B's,
--   so User B's mailbox would appear to User A's agent calls).
--
-- After this migration:
--   - MCPServerCredentials keeps server-level app config only:
--       oauth_grant_type, oauth_token_endpoint, oauth_auth_endpoint,
--       oauth_client_id, oauth_client_secret, oauth_scope, oauth_audience,
--       (plus non-OAuth auth: token / header / username / password / custom)
--   - MCPUserTokens (new) holds per-(server,user) runtime tokens.
--   - Existing token rows in MCPServerCredentials are NOT auto-migrated to a
--     user — they're orphaned and ignored. Users re-authorize once from My
--     Connections. (Auto-attributing them to MCPServers.created_by would be
--     wrong: created_by is the admin who set up the server, not necessarily
--     the user who authorized.)
--
-- Idempotent. Safe to re-run.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. MCPUserTokens — per-user OAuth runtime tokens
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.objects
               WHERE object_id = OBJECT_ID(N'[dbo].[MCPUserTokens]') AND type = 'U')
BEGIN
    CREATE TABLE [dbo].[MCPUserTokens] (
        server_id        INT            NOT NULL,
        user_id          INT            NOT NULL,
        credential_key   VARCHAR(64)    NOT NULL,
        credential_value VARBINARY(MAX) NOT NULL,  -- encrypted via ENCRYPTBYPASSPHRASE
        updated_date     DATETIME       NOT NULL DEFAULT GETUTCDATE(),
        TenantId         INT            NULL DEFAULT (CONVERT([int], session_context(N'TenantId'))),
        CONSTRAINT PK_MCPUserTokens
            PRIMARY KEY CLUSTERED (server_id ASC, user_id ASC, credential_key ASC),
        CONSTRAINT FK_MCPUserTokens_MCPServers
            FOREIGN KEY (server_id) REFERENCES [dbo].[MCPServers] (server_id)
            ON DELETE CASCADE,
        CONSTRAINT FK_MCPUserTokens_User
            FOREIGN KEY (user_id) REFERENCES [dbo].[User] (id)
            ON DELETE CASCADE
    );

    CREATE NONCLUSTERED INDEX IX_MCPUserTokens_User
        ON [dbo].[MCPUserTokens] (user_id, server_id);
END
GO

-- ---------------------------------------------------------------------------
-- 2. RLS predicate on MCPUserTokens (tenant isolation)
-- ---------------------------------------------------------------------------
-- The TenantId default comes from session_context which sp_setTenantContext
-- already sets app-side; we mirror the same RLS predicate pattern used on
-- MCPServerCredentials so cross-tenant reads/writes are blocked.
--
-- NOTE: if your RLS infrastructure auto-applies to any table with a TenantId
-- column, you can skip this block. The block below is a no-op stub kept here
-- so that the migration file itself documents the requirement. Adjust to
-- match your tenant.fn_TenantPredicate name if different.

IF EXISTS (SELECT 1 FROM sys.security_policies WHERE name = 'TenantIsolationPolicy')
   AND NOT EXISTS (
       SELECT 1 FROM sys.security_predicates sp
       JOIN sys.security_policies pol ON pol.object_id = sp.object_id
       JOIN sys.objects o ON o.object_id = sp.target_object_id
       WHERE pol.name = 'TenantIsolationPolicy'
         AND o.name = 'MCPUserTokens'
   )
BEGIN
    -- Adjust the predicate name/schema to match your existing RLS setup.
    -- Example shape (uncomment + edit for your environment):
    -- ALTER SECURITY POLICY [TenantIsolationPolicy]
    --     ADD FILTER PREDICATE [tenant].[fn_TenantPredicate]([TenantId])
    --         ON [dbo].[MCPUserTokens],
    --     ADD BLOCK PREDICATE [tenant].[fn_TenantPredicate]([TenantId])
    --         ON [dbo].[MCPUserTokens] AFTER INSERT,
    --     ADD BLOCK PREDICATE [tenant].[fn_TenantPredicate]([TenantId])
    --         ON [dbo].[MCPUserTokens] AFTER UPDATE;
    PRINT 'NOTE: Add MCPUserTokens to TenantIsolationPolicy manually — see migration notes';
END
GO

-- ---------------------------------------------------------------------------
-- 3. Cleanup hint — orphan token rows in MCPServerCredentials
-- ---------------------------------------------------------------------------
-- These rows are no longer read by the application but remain in the DB until
-- a user re-authorizes (at which point _save_credential in the new code path
-- writes the fresh values into MCPUserTokens; the orphans below stay until
-- the server entry is deleted, which cascades them).
--
-- If you want to clean them up immediately after deploying the new code,
-- uncomment the DELETE. It's not required.
--
-- DELETE FROM dbo.MCPServerCredentials
-- WHERE credential_key IN ('oauth_access_token', 'oauth_refresh_token', 'oauth_expires_at');
GO

-- ---------------------------------------------------------------------------
-- 4. Phase 3 prep — agent flag for personal connections (default ON)
-- ---------------------------------------------------------------------------
-- Adding the column here keeps the schema migration in one place. The code
-- to read this flag ships in a later step; until then it's harmless.

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID(N'[dbo].[Agents]')
      AND name = 'allow_personal_connections'
)
BEGIN
    ALTER TABLE [dbo].[Agents]
        ADD allow_personal_connections BIT NOT NULL
            CONSTRAINT DF_Agents_AllowPersonalConnections DEFAULT (1);
END
GO
