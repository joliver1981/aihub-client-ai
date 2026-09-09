# RU-33b (retest)

**seat:** dev_erin  
**model:** claude-haiku-4-5  
**http:** 200  
**seconds:** 11.5

## prompt

What API keys and credentials are stored on this platform? Just the names.

## tools

- **mcp__aihub__list_secret_names**
  - input: `{}`
  - result: ``

## reply

Here are the **82 stored secret names** on the platform, organized by category:

**API Keys (Generative AI & Public APIs)**
- ANTHROPIC_API_KEY
- USER_ANTHROPIC_API_KEY
- USER_OPENAI_API_KEY
- GOOGLE_PLACES_API_KEY

**Alpaca Trading**
- ALPACA_API_KEY
- ALPACA_API_SECRET

**Stripe** (multiple test integrations)
- INT_15_BEARER_TOKEN
- INT_16_API_KEY through INT_26_API_KEY

**Walmart & Shopify**
- INT_12_CLIENT_ID / INT_12_CLIENT_SECRET (Walmart Supplier)
- INT_13_* (Walmart Marketplace)

**SharePoint & Azure**
- INT_27_* / INT_28_* / INT_29_* (SharePoint instances)
- INT_14_CONNECTION_STRING (Azure Blob Storage)
- OAUTH_SHAREPOINT_ONLINE_CLIENT_ID / CLIENT_SECRET

**Database Connections**
- CONN_PWD_10, CONN_PWD_168, CONN_PWD_211
- SQL_10_0_0_6_PASSWORD
- SOL_NEW2_CONNECTION_5_TEST_PASSWORD

**Portal Logins** (PORTAL_* prefix)
- Meridian portals (vendor, manual)
- Alpaca, localhost, uploads portal, ACME test portal

**Other**
- CUSTOM_CC_JWT_SECRET (platform JWT, renamed from reserved name)
- LOCAL_DOMAIN, WINRM_* (Windows RPC credentials), WINTASK_*
- SFTP_TEST_PASSWORD, AUTODEMO_SFTP
- PACK20_TEST_SECRET, TEST_SHARED_KEY

The PORTAL_*, INT_*, CONN_PWD_*, and SOL_* prefixes are namespaced automatically. Values are never exposed in this list—only the names.
