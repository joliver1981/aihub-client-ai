# RU-11c

**seat:** ru_casey  
**model:** claude-haiku-4-5  
**http:** 200  
**seconds:** 10.6

## prompt

What external systems is AI Hub connected to that I can use?

## tools

- **mcp__aihub__list_integrations**
  - input: `{}`
  - result: `2 integration(s) exist on this install, but none are assigned to this user's groups — an admin can assign one to make it available.`
- **mcp__aihub__list_mcp_servers**
  - input: `{}`
  - result (FAILED): `MCP server inspection is available to Developer/Admin users only.`

## reply

Right now, there are **2 integrations configured on this AI Hub install, but neither is assigned to your groups yet** — so they are not available for you to use.

An admin would need to assign one (or both) of them to your group to unlock access. Integrations can connect to systems like:
- **SharePoint** (search files, download, upload)
- **Shopify** (product/order data)
- **Stripe** (payment/customer data)
- **Azure Blob Storage** (cloud file storage)
- Custom **APIs** (REST endpoints)
- And other business systems

**What you can do:**
1. **Ask an admin** which integrations are configured and request access to the ones you need
2. **In the meantime**, if you have files on a web portal (vendor invoice site, customer portal, etc.), I can log in and download/upload files with a real browser — no integration setup required (look for **portal workflows** if the task repeats)
3. **Your personal accounts** (Microsoft 365 Outlook inbox, calendar) can be connected at `/my-connections` on your own

Would you like help understanding what an admin would need to do, or would you rather explore other ways to access external data?
