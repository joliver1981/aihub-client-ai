-- ============================================================================
-- Document Categories + Group Access — Database Migration (document search v3)
--
-- PURPOSE
--   Generally-available documents (Documents.is_knowledge_document = 0) become
--   visible to a user only when one of that user's groups has been granted the
--   CATEGORY that the document's document_type belongs to.
--
--   Private agent knowledge (is_knowledge_document = 1) is NOT affected by this
--   migration in any way. It keeps its existing per-agent / per-user isolation
--   and its brute-force retrieval path.
--
-- WHY A CATEGORY LAYER
--   document_type is free text assigned by an LLM at ingest, so it drifts:
--   lease_agreement / commercial_lease_agreement / retail_lease_agreement /
--   lease_amendment are one business concept spelled four ways. Granting on a
--   CATEGORY means an admin grants "Leases" once and every present and future
--   spelling that maps into it inherits the grant.
--
--   The safety property that matters: a document_type with NO category row is
--   granted to NOBODY, so a hallucinated new type is invisible to non-admins
--   rather than silently visible to everyone. The classifier can never widen
--   access -- only fail to widen it, which an admin then fixes.
--
-- SEMANTICS (mirrors the AgentGroups / UserGroups convention)
--   - Admin (role >= 3)                      -> unrestricted, these tables are not consulted.
--   - A category granted to >= 1 of my groups -> I can read every document_type in it.
--   - A document_type with no category row    -> readable by admins only.
--   - A category with no grant row            -> readable by admins only.
--
-- ROLLOUT SAFETY
--   The seed below creates ONE CATEGORY PER EXISTING document_type (1:1) and
--   grants every category to every existing group. So immediately after this
--   migration runs, every user sees exactly what they see today -- enabling the
--   feature is a no-op until an admin deliberately narrows a grant or merges
--   types into a coarser category. There is no lock-out window.
--
-- REQUIRES a DDL-capable login. The application login (TenantAppUser) has
--   full DML but no CREATE TABLE / ALTER -- verified 2026-08-12. Run this the
--   same way migration 008 was run.
--
-- Mirrors the [dbo].[AgentDocumentTypes] (008) and [dbo].[AgentGroups]
-- conventions, including the TenantId / session_context default from
-- 004_command_center.sql.
-- ============================================================================

SET NOCOUNT ON;
GO

-- ---------------------------------------------------------------------------
-- 1. Categories — the coarse business concept an admin actually grants.
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.objects
               WHERE object_id = OBJECT_ID(N'[dbo].[DocumentCategories]') AND type = 'U')
BEGIN
    CREATE TABLE [dbo].[DocumentCategories] (
        category_id     INT           IDENTITY(1,1) PRIMARY KEY,
        TenantId        INT           NOT NULL DEFAULT (CONVERT([int], session_context(N'TenantId'))),
        category_slug   VARCHAR(100)  NOT NULL,   -- stable machine key, e.g. 'leases'
        category_name   NVARCHAR(200) NOT NULL,   -- what the admin sees, e.g. 'Leases'
        description     NVARCHAR(500) NULL,
        is_system       BIT           NOT NULL DEFAULT 0,  -- 1 = seeded 1:1 from an existing type
        create_date     DATETIME      NOT NULL DEFAULT GETDATE(),
        created_by      VARCHAR(150)  NULL,

        CONSTRAINT UQ_DocumentCategories_slug UNIQUE (TenantId, category_slug)
    );

    PRINT 'Created table: DocumentCategories';
END
GO

-- ---------------------------------------------------------------------------
-- 2. document_type -> category. A type belongs to exactly one category.
--    Merging drift (commercial_lease_agreement -> leases) is an UPDATE here.
--
--    assigned_by / status support AI-managed categorisation:
--      AI-managed ON  (default) -> AI writes status='active', posts a My Work INFO item.
--      AI-managed OFF           -> AI writes status='pending', posts a My Work approval task.
--      Low AI confidence        -> status='pending' even when AI-managed is ON.
--    ONLY status='active' rows are consulted by the access resolver, so a
--    pending mapping means the type is admin-only until someone confirms it.
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.objects
               WHERE object_id = OBJECT_ID(N'[dbo].[DocumentTypeCategories]') AND type = 'U')
BEGIN
    CREATE TABLE [dbo].[DocumentTypeCategories] (
        id              INT          IDENTITY(1,1) PRIMARY KEY,
        TenantId        INT          NOT NULL DEFAULT (CONVERT([int], session_context(N'TenantId'))),
        document_type   VARCHAR(100) NOT NULL,   -- matches Documents.document_type length
        category_id     INT          NOT NULL,
        status          VARCHAR(20)  NOT NULL DEFAULT 'active',  -- 'active' | 'pending'
        assigned_by     VARCHAR(20)  NOT NULL DEFAULT 'human',   -- 'ai' | 'human' | 'migration'
        ai_confidence   FLOAT        NULL,       -- populated when assigned_by = 'ai'
        create_date     DATETIME     NOT NULL DEFAULT GETDATE(),
        created_by      VARCHAR(150) NULL,
        reviewed_by     VARCHAR(150) NULL,
        reviewed_at     DATETIME     NULL,

        CONSTRAINT FK_DocumentTypeCategories_Category
            FOREIGN KEY (category_id) REFERENCES [dbo].[DocumentCategories](category_id)
            ON DELETE CASCADE,

        CONSTRAINT CK_DocumentTypeCategories_status
            CHECK (status IN ('active','pending')),

        -- One category per type. This is what makes the resolver a simple join
        -- and makes "which category is this document in" unambiguous.
        CONSTRAINT UQ_DocumentTypeCategories_type UNIQUE (TenantId, document_type)
    );

    -- Read pattern: given the categories a user can see, list their document_types.
    CREATE NONCLUSTERED INDEX IX_DocumentTypeCategories_category
        ON [dbo].[DocumentTypeCategories] (TenantId, category_id)
        INCLUDE (document_type);

    PRINT 'Created table: DocumentTypeCategories';
END
GO

-- ---------------------------------------------------------------------------
-- 3. The grant: category -> group. Mirrors AgentGroups exactly.
--
--    A row grants READ access to the category. can_manage additionally makes
--    the group the category's STEWARD: its members receive the My Work items
--    for new type assignments in this category and can recategorise types.
--    So "Finance manages Leases" is one row with can_manage = 1.
--    Stewardship implies read access -- there is no manage-without-see.
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.objects
               WHERE object_id = OBJECT_ID(N'[dbo].[DocumentCategoryGroups]') AND type = 'U')
BEGIN
    CREATE TABLE [dbo].[DocumentCategoryGroups] (
        id              INT          IDENTITY(1,1) PRIMARY KEY,
        TenantId        INT          NOT NULL DEFAULT (CONVERT([int], session_context(N'TenantId'))),
        category_id     INT          NOT NULL,
        group_id        INT          NOT NULL,
        can_manage      BIT          NOT NULL DEFAULT 0,  -- 1 = this group stewards the category
        create_date     DATETIME     NOT NULL DEFAULT GETDATE(),
        created_by      VARCHAR(150) NULL,

        CONSTRAINT FK_DocumentCategoryGroups_Category
            FOREIGN KEY (category_id) REFERENCES [dbo].[DocumentCategories](category_id)
            ON DELETE CASCADE,
        CONSTRAINT FK_DocumentCategoryGroups_Groups
            FOREIGN KEY (group_id) REFERENCES [dbo].[Groups](id) ON DELETE CASCADE,

        CONSTRAINT UQ_DocumentCategoryGroups UNIQUE (category_id, group_id)
    );

    -- Read pattern: given a user's group ids, list the categories they can see.
    CREATE NONCLUSTERED INDEX IX_DocumentCategoryGroups_group
        ON [dbo].[DocumentCategoryGroups] (TenantId, group_id)
        INCLUDE (category_id);

    PRINT 'Created table: DocumentCategoryGroups';
END
GO

-- ============================================================================
-- SEED — idempotent, and deliberately a no-op for every existing user.
--
-- Run once per tenant. @TenantId defaults to the session context when the
-- connection sets it (the app's convention) and falls back to the distinct
-- TenantId already present on Documents.
-- ============================================================================

DECLARE @TenantId INT = CONVERT(INT, SESSION_CONTEXT(N'TenantId'));
PRINT(@TenantId)
IF @TenantId IS NULL
    SELECT TOP (1) @TenantId = TenantId FROM [dbo].[Documents] WHERE TenantId IS NOT NULL;

IF @TenantId IS NULL
BEGIN
    PRINT 'SEED SKIPPED: could not determine TenantId. Set it and re-run the seed block.';
END
ELSE
BEGIN
    PRINT CONCAT('Seeding document categories for TenantId = ', @TenantId);

    -- 3a. One category per existing document_type, 1:1. A 1:1 category is
    --     indistinguishable from granting the raw type, so this introduces no
    --     behavior change -- it just gives admins something mergeable later.
    INSERT INTO [dbo].[DocumentCategories] (TenantId, category_slug, category_name, is_system, created_by)
    SELECT DISTINCT
           @TenantId,
           d.document_type,
           d.document_type,
           1,
           'migration_016'
    FROM   [dbo].[Documents] d
    WHERE  d.TenantId = @TenantId
      AND  d.document_type IS NOT NULL
      AND  LEN(LTRIM(RTRIM(d.document_type))) > 0
      AND  NOT EXISTS (SELECT 1 FROM [dbo].[DocumentCategories] c
                       WHERE c.TenantId = @TenantId AND c.category_slug = d.document_type);

    PRINT CONCAT('  categories seeded (new this run): ', @@ROWCOUNT);

    -- 3b. Map each type to its own seeded category, active immediately.
    INSERT INTO [dbo].[DocumentTypeCategories]
           (TenantId, document_type, category_id, status, assigned_by, created_by)
    SELECT c.TenantId, c.category_slug, c.category_id, 'active', 'migration', 'migration_016'
    FROM   [dbo].[DocumentCategories] c
    WHERE  c.TenantId = @TenantId
      AND  c.is_system = 1
      AND  NOT EXISTS (SELECT 1 FROM [dbo].[DocumentTypeCategories] tc
                       WHERE tc.TenantId = @TenantId AND tc.document_type = c.category_slug);

    PRINT CONCAT('  type->category mappings seeded: ', @@ROWCOUNT);

    -- 3c. Grant every category to every existing group.
    --     THIS IS WHAT MAKES THE MIGRATION A NO-OP: after it runs, every user
    --     sees exactly what they saw before. Admins then REMOVE grants to
    --     restrict, rather than having to ADD grants to restore access.
    INSERT INTO [dbo].[DocumentCategoryGroups] (TenantId, category_id, group_id, created_by)
    SELECT @TenantId, c.category_id, g.id, 'migration_016'
    FROM   [dbo].[DocumentCategories] c
    CROSS JOIN [dbo].[Groups] g
    WHERE  c.TenantId = @TenantId
      AND  g.TenantId = @TenantId
      AND  NOT EXISTS (SELECT 1 FROM [dbo].[DocumentCategoryGroups] cg
                       WHERE cg.category_id = c.category_id AND cg.group_id = g.id);

    PRINT CONCAT('  category->group grants seeded: ', @@ROWCOUNT);
END
GO

-- ============================================================================
-- VERIFY — the exact query the application resolver runs.
-- Returns the document_type allow list for one user. Substitute a real user id.
-- An admin (User.role >= 3) never reaches this query; the app short-circuits.
-- ============================================================================
/*
DECLARE @user_id INT = 12;

SELECT DISTINCT tc.document_type
FROM   [dbo].[DocumentTypeCategories] tc
JOIN   [dbo].[DocumentCategoryGroups] cg ON cg.category_id = tc.category_id
                                        AND cg.TenantId   = tc.TenantId
JOIN   [dbo].[UserGroups]             ug ON ug.group_id    = cg.group_id
                                        AND ug.TenantId    = cg.TenantId
WHERE  ug.user_id = @user_id
  AND  tc.status  = 'active'        -- pending assignments are admin-only
ORDER  BY tc.document_type;

-- Who stewards a category (receives its My Work items, may recategorise):
SELECT c.category_name, g.group_name
FROM   [dbo].[DocumentCategoryGroups] cg
JOIN   [dbo].[DocumentCategories] c ON c.category_id = cg.category_id
JOIN   [dbo].[Groups]             g ON g.id          = cg.group_id
WHERE  cg.can_manage = 1
ORDER  BY c.category_name, g.group_name;
*/

-- ============================================================================
-- ROLLBACK — drop in reverse dependency order. Removing these tables restores
-- the previous behavior only if the application flag is also turned off, since
-- the resolver fails CLOSED when it cannot read its grants.
-- ============================================================================
/*
DROP TABLE IF EXISTS [dbo].[DocumentCategoryGroups];
DROP TABLE IF EXISTS [dbo].[DocumentTypeCategories];
DROP TABLE IF EXISTS [dbo].[DocumentCategories];
*/
