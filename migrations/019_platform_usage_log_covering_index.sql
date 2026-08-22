-- ============================================================================
-- Migration 019: covering index for the monthly request count on PlatformUsageLog
--
-- PURPOSE
--   Two hot queries count a tenant's requests for the current month:
--     * tenant app, admin_tier_usage.get_agent_user_env_info()
--         SELECT COUNT(DISTINCT RequestId) FROM PlatformUsageLog
--         WHERE TokensUsed > 0 AND RequestTimestamp >= <month start>
--           AND RequestTimestamp < <next month start>       (+ RLS tenant predicate)
--     * cloud relay, aihub-api rate_limiter (on every LLM call)
--         SELECT COUNT(DISTINCT RequestId) FROM PlatformUsageLog
--         WHERE TenantId = ? AND RequestTimestamp in the current month
--   IX_PlatformUsageLog_RequestTimestamp is keyed on RequestTimestamp ONLY, so the
--   month-range seek needs one key lookup per row into the clustered index (rows
--   carry NVARCHAR(MAX) RequestBody / ErrorMessage) to read TokensUsed, RequestId
--   and TenantId. Measured 2026-08-21 on the shared S1 (20 DTU) database: 7,443
--   month rows for one tenant -> 30-230 s per run when cold (95 runs that day,
--   p50 57 s), 0.05 s warm; the relay-shaped query 26.8 s. It was the single
--   largest IO consumer on the database (docs/doc-api-concurrency-and-fast-busy.md
--   sections 2 and 7) and a major contributor to the document-store stalls.
--
-- WHAT IT DOES
--   Rebuilds IX_PlatformUsageLog_RequestTimestamp in place (same name, same key)
--   with INCLUDE (TokensUsed, RequestId, TenantId), so both queries are answered
--   from the index alone - no key lookups. The RLS tenant predicate
--   (tenant.sp_setTenantContext / tenant.fn_getTenantId) is evaluated against the
--   included TenantId.
--   Leading on RequestTimestamp rather than TenantId is deliberate: the RLS
--   predicate is NOT a seek predicate (measured: COUNT(*) under RLS alone 6.4 s
--   vs 0.16 s with an explicit TenantId = ?), so a TenantId-leading key would
--   degrade the tenant app's query to a full index scan.
--   Rebuilding the existing index (DROP_EXISTING) rather than adding a second
--   one keeps the per-INSERT write cost unchanged (the relay inserts one row per
--   LLM call). ONLINE = ON keeps those inserts flowing during the build.
--   Idempotent: skipped when the index already carries the three INCLUDE
--   columns; created fresh (covering) if the index is missing altogether.
--
-- REQUIRES a DDL-capable login (CREATE/ALTER INDEX). The application login
--   (TenantAppUser) has DML only - run it the same way 016-018 were run, ideally
--   off-peak: the build reads the whole table once on an IO-governed tier.
--
-- ROLLBACK (manual, restores the original non-covering definition):
--   CREATE NONCLUSTERED INDEX IX_PlatformUsageLog_RequestTimestamp
--       ON [dbo].[PlatformUsageLog] (RequestTimestamp)
--       WITH (DROP_EXISTING = ON, ONLINE = ON);
--
-- Follows the conventions of 018_document_records_delete_cascade.sql.
-- ============================================================================

SET NOCOUNT ON;
GO

IF OBJECT_ID(N'[dbo].[PlatformUsageLog]', 'U') IS NULL
BEGIN
    PRINT '019: [dbo].[PlatformUsageLog] does not exist in this database - nothing to do.';
END
ELSE
BEGIN
    DECLARE @index_id INT, @covered INT = 0, @key_ok INT = 0;

    SELECT @index_id = i.index_id
    FROM sys.indexes i
    WHERE i.object_id = OBJECT_ID(N'[dbo].[PlatformUsageLog]')
      AND i.name = N'IX_PlatformUsageLog_RequestTimestamp';

    IF @index_id IS NOT NULL
    BEGIN
        -- The key must be exactly (RequestTimestamp) ...
        SELECT @key_ok = CASE WHEN COUNT(*) = 1
                               AND MAX(c.name) = N'RequestTimestamp' THEN 1 ELSE 0 END
        FROM sys.index_columns ic
        JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
        WHERE ic.object_id = OBJECT_ID(N'[dbo].[PlatformUsageLog]')
          AND ic.index_id = @index_id
          AND ic.is_included_column = 0;

        -- ... and the three columns must be INCLUDEd.
        SELECT @covered = COUNT(*)
        FROM sys.index_columns ic
        JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
        WHERE ic.object_id = OBJECT_ID(N'[dbo].[PlatformUsageLog]')
          AND ic.index_id = @index_id
          AND ic.is_included_column = 1
          AND c.name IN (N'TokensUsed', N'RequestId', N'TenantId');
    END

    IF @index_id IS NOT NULL AND @key_ok = 1 AND @covered = 3
    BEGIN
        PRINT '019: IX_PlatformUsageLog_RequestTimestamp already covers TokensUsed/RequestId/TenantId - nothing to do.';
    END
    ELSE IF @index_id IS NOT NULL
    BEGIN
        PRINT '019: rebuilding IX_PlatformUsageLog_RequestTimestamp with INCLUDE (TokensUsed, RequestId, TenantId) ONLINE...';
        CREATE NONCLUSTERED INDEX IX_PlatformUsageLog_RequestTimestamp
            ON [dbo].[PlatformUsageLog] (RequestTimestamp)
            INCLUDE (TokensUsed, RequestId, TenantId)
            WITH (DROP_EXISTING = ON, ONLINE = ON);
        PRINT '019: done - month-range request counts on PlatformUsageLog no longer need key lookups.';
    END
    ELSE
    BEGIN
        PRINT '019: IX_PlatformUsageLog_RequestTimestamp is missing - creating it as a covering index ONLINE...';
        CREATE NONCLUSTERED INDEX IX_PlatformUsageLog_RequestTimestamp
            ON [dbo].[PlatformUsageLog] (RequestTimestamp)
            INCLUDE (TokensUsed, RequestId, TenantId)
            WITH (ONLINE = ON);
        PRINT '019: done.';
    END
END
GO
