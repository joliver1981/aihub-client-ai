-- ============================================================================
-- On-the-fly Automations — asset + run history tables
--
-- An Automation is a persisted, versioned, AI-generated Python solution:
-- code + manifest on disk under automations/tenant_<id>/<automation_id>/,
-- runtime = a dedicated agent environment (one env per automation), runs are
-- subprocess executions of a FROZEN (pinned) version. See
-- docs/on-the-fly-automations-plan.md.
--
-- Design decisions baked in here (James, 2026-07-13):
--   * one agent environment per automation (environment_id column)
--   * schedules/API run the PINNED version (pinned_version), never silently
--     the latest edit; "promote" moves the pin
--   * concurrent runs are skipped, recorded as status='skipped'
--
-- Code/manifest bytes are NOT in the DB — the DB is the registry (listing,
-- authz, run history); the filesystem owns the versioned code, mirroring the
-- agent_environments layout.
--
-- Idempotent. Safe to re-run. The app also ensures these tables at startup
-- (automations/manager.py ensure_tables) so dev installs work without a
-- manual migration step; this file is the production record.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Automations — the asset registry
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.objects
               WHERE object_id = OBJECT_ID(N'[dbo].[Automations]') AND type = 'U')
BEGIN
    CREATE TABLE [dbo].[Automations] (
        automation_id   VARCHAR(36)    NOT NULL,            -- uuid4
        name            NVARCHAR(200)  NOT NULL,
        description     NVARCHAR(MAX)  NULL,
        owner_user_id   INT            NOT NULL,
        environment_id  VARCHAR(100)   NULL,                -- AgentEnvironments id (one env per automation); NULL = bundle-python fallback
        current_version INT            NOT NULL DEFAULT 0,  -- latest saved version (0 = no code yet)
        pinned_version  INT            NOT NULL DEFAULT 0,  -- version that runs (0 = nothing promoted yet)
        status          VARCHAR(20)    NOT NULL DEFAULT 'active',   -- active | deleted
        created_at      DATETIME       NOT NULL DEFAULT GETUTCDATE(),
        updated_at      DATETIME       NOT NULL DEFAULT GETUTCDATE(),
        TenantId        INT            NULL DEFAULT (CONVERT([int], session_context(N'TenantId'))),
        CONSTRAINT PK_Automations PRIMARY KEY CLUSTERED (automation_id ASC),
        CONSTRAINT FK_Automations_Owner
            FOREIGN KEY (owner_user_id) REFERENCES [dbo].[User] (id)
    );

    -- Name is unique per tenant among live automations (soft-deleted names free up).
    CREATE UNIQUE NONCLUSTERED INDEX UX_Automations_TenantName
        ON [dbo].[Automations] (TenantId, name)
        WHERE status = 'active';
END
GO

-- ---------------------------------------------------------------------------
-- 2. AutomationRuns — honest run history
-- ---------------------------------------------------------------------------
-- status lifecycle: running -> success | failed | unverified
--   plus terminal-on-insert: skipped (concurrent-run guard fired)
--   * success    = exit 0 AND all declared outputs verified
--   * failed     = nonzero exit, timeout, OR a declared output missing
--   * unverified = exit 0 but some declared output could not be checked
--                  (e.g. remote upload verification not yet implemented)
-- Never report success from the absence of an exception — see the
-- silent-success remediation plan.
IF NOT EXISTS (SELECT 1 FROM sys.objects
               WHERE object_id = OBJECT_ID(N'[dbo].[AutomationRuns]') AND type = 'U')
BEGIN
    CREATE TABLE [dbo].[AutomationRuns] (
        run_id          VARCHAR(36)    NOT NULL,            -- uuid4
        automation_id   VARCHAR(36)    NOT NULL,
        version         INT            NOT NULL,            -- frozen version executed
        trigger_source  VARCHAR(20)    NOT NULL,            -- manual | api | dry_run | schedule | workflow | email
        status          VARCHAR(20)    NOT NULL DEFAULT 'running',
        exit_code       INT            NULL,
        requested_by    INT            NULL,                -- User.id when user-triggered
        inputs_json     NVARCHAR(MAX)  NULL,                -- manifest inputs as provided for this run
        verify_report   NVARCHAR(MAX)  NULL,                -- JSON: per-declared-output check results
        output_files    NVARCHAR(MAX)  NULL,                -- JSON list of files produced in the run workdir
        log_path        NVARCHAR(500)  NULL,                -- run.log inside the run workdir
        error           NVARCHAR(MAX)  NULL,                -- runner-level error (not script stderr; that's in the log)
        started_at      DATETIME       NOT NULL DEFAULT GETUTCDATE(),
        finished_at     DATETIME       NULL,
        TenantId        INT            NULL DEFAULT (CONVERT([int], session_context(N'TenantId'))),
        CONSTRAINT PK_AutomationRuns PRIMARY KEY CLUSTERED (run_id ASC),
        CONSTRAINT FK_AutomationRuns_Automation
            FOREIGN KEY (automation_id) REFERENCES [dbo].[Automations] (automation_id)
            ON DELETE CASCADE
    );

    CREATE NONCLUSTERED INDEX IX_AutomationRuns_Automation
        ON [dbo].[AutomationRuns] (automation_id, started_at DESC);

    -- The skip-if-running guard queries for a live 'running' row.
    CREATE NONCLUSTERED INDEX IX_AutomationRuns_Running
        ON [dbo].[AutomationRuns] (automation_id, status)
        INCLUDE (started_at)
        WHERE status = 'running';
END
GO

-- ---------------------------------------------------------------------------
-- 3. RLS — tenant isolation
-- ---------------------------------------------------------------------------
-- TenantId defaults from session_context (set app-side via
-- tenant.sp_setTenantContext, same as every other tenant table). If your
-- install applies the TenantIsolationPolicy security policy per-table, add
-- both tables to it — same note as migration 013 (MCPUserTokens).
IF EXISTS (SELECT 1 FROM sys.security_policies WHERE name = 'TenantIsolationPolicy')
   AND NOT EXISTS (
       SELECT 1 FROM sys.security_predicates sp
       JOIN sys.security_policies pol ON pol.object_id = sp.object_id
       JOIN sys.objects o ON o.object_id = sp.target_object_id
       WHERE pol.name = 'TenantIsolationPolicy'
         AND o.name IN ('Automations', 'AutomationRuns')
   )
BEGIN
    PRINT 'NOTE: Add Automations + AutomationRuns to TenantIsolationPolicy manually — see migration notes';
END
GO
