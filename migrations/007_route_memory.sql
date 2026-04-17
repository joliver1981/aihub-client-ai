-- ============================================================================
-- Migration 007: Route Memory
-- Lightweight query-to-route log for the Command Center.
-- Each row records one observed query routing (user, query, canonical form,
-- intent, agent, success/fail, latency).  Over time the system learns
-- which agent handles which types of queries and shortcuts the routing.
-- ============================================================================

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[cc_RouteMemory]') AND type = 'U')
BEGIN
    CREATE TABLE [dbo].[cc_RouteMemory] (
        id               INT IDENTITY(1,1) PRIMARY KEY,
        TenantId         INT             NOT NULL DEFAULT (CONVERT([int], session_context(N'TenantId'))),
        user_id          INT             NOT NULL,
        query_text       NVARCHAR(500)   NOT NULL,
        normalized_query NVARCHAR(200)   NULL,      -- LLM-generated canonical form (e.g. "sales by region")
        intent           VARCHAR(50)     NOT NULL,   -- chat/query/analyze/build/delegate/multi_step
        agent_id         VARCHAR(50)     NULL,
        agent_name       NVARCHAR(200)   NULL,
        route_path       VARCHAR(200)    NULL,       -- e.g. "classify_intent->gather_data->agent_14"
        success          BIT             NOT NULL DEFAULT 1,
        latency_ms       INT             NULL,
        created_at       DATETIME        NOT NULL DEFAULT GETUTCDATE()
    );

    CREATE NONCLUSTERED INDEX IX_cc_RouteMemory_Lookup
        ON [dbo].[cc_RouteMemory] (TenantId, user_id, normalized_query)
        INCLUDE (agent_id, agent_name, intent, success);

    CREATE NONCLUSTERED INDEX IX_cc_RouteMemory_Recent
        ON [dbo].[cc_RouteMemory] (TenantId, user_id, created_at DESC);

    PRINT 'Created table: cc_RouteMemory';
END
GO
