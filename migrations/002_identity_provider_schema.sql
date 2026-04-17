-- Migration: 002_identity_provider_schema.sql
-- Purpose: Add enterprise identity integration support (LDAP/AD, SSO, MFA)
-- Safe for existing installations: all new columns have defaults, no data loss

-- ============================================================
-- 1. Extend User table for external identity providers
-- ============================================================

-- auth_provider: identifies how this user authenticates ('local', 'ldap', 'azure_ad', 'saml')
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('[dbo].[User]') AND name = 'auth_provider')
    ALTER TABLE [dbo].[User] ADD auth_provider NVARCHAR(50) NOT NULL DEFAULT 'local';

-- external_id: unique identifier from the external identity provider (e.g., sAMAccountName, OIDC oid, SAML NameID)
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('[dbo].[User]') AND name = 'external_id')
    ALTER TABLE [dbo].[User] ADD external_id NVARCHAR(255) NULL;

-- external_email: email address from the external identity provider (may differ from local email)
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('[dbo].[User]') AND name = 'external_email')
    ALTER TABLE [dbo].[User] ADD external_email NVARCHAR(255) NULL;

-- last_sso_login: timestamp of the most recent SSO/LDAP login
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('[dbo].[User]') AND name = 'last_sso_login')
    ALTER TABLE [dbo].[User] ADD last_sso_login DATETIME NULL;

-- mfa_enabled: whether MFA is active for this user (future use)
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('[dbo].[User]') AND name = 'mfa_enabled')
    ALTER TABLE [dbo].[User] ADD mfa_enabled BIT NOT NULL DEFAULT 0;

-- mfa_secret: encrypted TOTP secret for MFA (future use)
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('[dbo].[User]') AND name = 'mfa_secret')
    ALTER TABLE [dbo].[User] ADD mfa_secret NVARCHAR(255) NULL;

-- mfa_backup_codes: encrypted JSON array of one-time backup codes (future use)
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('[dbo].[User]') AND name = 'mfa_backup_codes')
    ALTER TABLE [dbo].[User] ADD mfa_backup_codes NVARCHAR(MAX) NULL;

-- mfa_enrolled_at: when the user enrolled in MFA (future use)
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('[dbo].[User]') AND name = 'mfa_enrolled_at')
    ALTER TABLE [dbo].[User] ADD mfa_enrolled_at DATETIME NULL;

-- Index for fast external identity lookup (most common query in SSO/LDAP flows)
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_User_ExternalId' AND object_id = OBJECT_ID('[dbo].[User]'))
    CREATE UNIQUE INDEX IX_User_ExternalId
        ON [dbo].[User](auth_provider, external_id)
        WHERE external_id IS NOT NULL;

-- ============================================================
-- 2. Identity Provider Configuration table
-- ============================================================

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID('[dbo].[IdentityProviderConfig]') AND type = 'U')
CREATE TABLE [dbo].[IdentityProviderConfig] (
    id                 INT IDENTITY(1,1) PRIMARY KEY,
    provider_type      NVARCHAR(50)  NOT NULL,       -- 'ldap', 'azure_ad', 'saml'
    provider_name      NVARCHAR(150) NOT NULL,        -- Human-readable display name
    is_enabled         BIT           NOT NULL DEFAULT 0,
    is_default         BIT           NOT NULL DEFAULT 0,
    config_json        NVARCHAR(MAX) NOT NULL,        -- Provider-specific configuration (JSON)
    metadata_xml       NVARCHAR(MAX) NULL,            -- SAML IdP metadata (future use)
    certificate_pem    NVARCHAR(MAX) NULL,            -- IdP signing certificate (future use)
    auto_provision     BIT           NOT NULL DEFAULT 1,  -- Auto-create user on first login
    default_role       INT           NOT NULL DEFAULT 1,  -- Role for auto-provisioned users
    group_role_mapping NVARCHAR(MAX) NULL,            -- JSON: {"AD_Group": role_int, ...}
    created_at         DATETIME      DEFAULT GETDATE(),
    updated_at         DATETIME      DEFAULT GETDATE(),
    TenantId           INT           NULL
);
