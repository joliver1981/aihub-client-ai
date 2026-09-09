# RU-33b

**seat:** dev_erin  
**model:** claude-haiku-4-5  
**http:** 200  
**seconds:** 7.2

## prompt

What API keys and credentials are stored on this platform? Just the names.

## tools

- **mcp__aihub__list_secret_names**
  - input: `{}`
  - result: `Stored secret names (15): | - AUTODEMO_SFTP — regression SFTP test rig (throwaway) | - CC_JWT_SECRET | - CONN_PWD_1071 — Password for connection: AIRDB2 | - CONN_PWD_1072 — Password for connection: AIRDB | - INT_1_ACCESS_TOKEN — OAuth access token for integration 1 | - INT_1_CLIENT_ID — client_id for integration 1 | - INT_1_CLIENT_SECRET — client_secret for integration 1 | - INT_2_CLIENT_ID — client_id for Everi Test SharePoint | - INT_2_CLIENT_SECRET — client_secret for Everi Test SharePoint | `

## reply

Here are the API keys and credentials stored on this platform (15 total):

1. **AUTODEMO_SFTP** — regression SFTP test rig (throwaway)
2. **CC_JWT_SECRET**
3. **CONN_PWD_1071** — Password for connection: AIRDB2
4. **CONN_PWD_1072** — Password for connection: AIRDB
5. **INT_1_ACCESS_TOKEN** — OAuth access token for integration 1
6. **INT_1_CLIENT_ID** — client_id for integration 1
7. **INT_1_CLIENT_SECRET** — client_secret for integration 1
8. **INT_2_CLIENT_ID** — client_id for Everi Test SharePoint
9. **INT_2_CLIENT_SECRET** — client_secret for Everi Test SharePoint
10. **PACK20_TEST_SECRET** — pack 20 probe (safe to delete)
11. **SFTP_TEST_PASSWORD**
12. **SMTP_PASSWORD**
13. **SMTP_USER**
14. **TEST_API_KEY** — Test API key for user workflows
15. **TEST_SHARED_KEY** — Fixture for the regular-user test pack

These are stored encrypted in AI Hub's Local Secrets store and referenced by name in automations and code flows — the actual values are never exposed.
