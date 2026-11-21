# AI Hub v2.4.0

Includes everything released since v2.3.4.

## Single sign-on with SAML (Microsoft Entra ID)

- **Sign in with your identity provider** — an Admin adds a **SAML single sign-on** provider under **Identity & Security**. While it is switched on, the sign-in page shows a "Sign in with <name>" button under the password form. The password form and local accounts work as before.
- **Set-up from the metadata address** — the form shows the Identifier and Reply URL to enter in Entra, and loads the identity provider's identifier, login URL and signing certificates from its App Federation Metadata Url. They can also be typed in.
- **Test sign-in** — runs a real sign-in against the saved settings and shows what came back and what AI Hub would do with it. It signs nobody in, changes nothing, and works before the provider is switched on.
- **Who a sign-in becomes** — a user already linked to that identity, otherwise an existing user with the same user name, otherwise one with the same email address. A matched user keeps its role and groups. When nobody matches, a new user is created under the same auto-provision switch and group mapping as directory (LDAP) sign-in. Entra sends groups as Object IDs, so group mappings use those.
- **User name domain** — makes `jsmith@company.com` the AI Hub user `jsmith` for that domain, the short name directory sign-in uses.
- Every answer is checked: signed by a saved certificate, from the configured issuer, for this AI Hub, inside its time window and used once. Sign-in started at the identity provider is off unless the Admin allows it.
- SAML needs AI Hub published over HTTPS. Not included: single logout, encrypted assertions and SHA-1 signatures.

## Knowledge search

- **Search by customer** — when an agent holds documents for several customers, a question that names one is searched in that customer's documents, and a comparison is searched once per customer. Short names and misspellings are matched to the customers on record, so it no longer depends on how the files are named. Documents with no customer attached are still searched and are labelled as such. Kill switch: `KNOWLEDGE_SCOPE_ENABLED=false`.
- **Attributes on knowledge documents** — a document can carry a customer and any other attributes (for example `region=East; department=Logistics`). Set them when adding a document on the agent's **Knowledge** page, edit them later in the document's dialog, or through `GET/POST /api/agent_knowledge/<id>/attributes`. Attributes show as badges in the document list. `KNOWLEDGE_SCOPE_ATTRIBUTES` (default `customer`) lists the attributes that steer the search.
- **Fixed:** about a third of indexed passages carried the page number of the next page. Passages are now labelled with the page that holds their text, and both search engines check the page at search time, so documents indexed earlier are searched correctly without being indexed again.
- **Fixed:** a rule in the lower half of a page lost to pages that only start on the topic, and long pages were cut short without saying so. Pages are now given whole, and a page shown in part or left out is named.
- **Fixed:** in a comparison across several documents, a document that held the value could be reported as "not found".
- **Fixed:** naming a company no longer locks the search onto one of its documents when the agent holds several.

## Compliance

- **A newer version replaces the older one** — when a document is a newer version of a manual the agent already holds for that customer, the older version is taken out of the agent's knowledge. A document older than the one held is recorded as a version but not added. Nothing is deleted: the documents, pages and version records are kept, and the knowledge entry says when and by what it was superseded. On by default; switch it off per **Compliance Process** node ("Replace older versions in the agent's knowledge") or platform wide with `COMPLIANCE_RETIRE_SUPERSEDED_VERSIONS=false`.
- **Customer attached automatically** — every document the Compliance Process node sends to an agent carries its customer, taken from the node's Retailer Name. Documents processed before this release are covered from their compliance records. The node has a new optional **Document Attributes** setting for further attributes.
- **Fixed:** a file that had already been processed was never recognised as a duplicate, so a re-sent manual was ingested and filed again as a new version. It now returns in seconds as a duplicate, and the run says what the agent holds of that file. A document processed before this release is recognised from the second time it is re-sent.

## The Agent

- **Asks the agent that holds the answer** — The Agent now sees which agents hold their own knowledge documents and consults the right one by itself. A document search also names the agents whose knowledge it did not cover.
- **Attributes from chat** — The Agent can attach attributes when it adds a knowledge document to an agent, and change them later.
- **Hide the Email screen by group** — an Admin can hide The Agent's Email screen from everyone or from chosen groups (the "email screen" line in the Admin rows at the bottom of the rail). It applies to Admins in those groups too. Addresses that already exist keep receiving mail.
- **Fixed:** while The Agent ran a Python step (including Excel, CSV and PDF exports), the whole service stopped answering for every user until the step ended. The step now runs beside the service. Kill switch: `AGENT_RUN_PYTHON_OFFLOAD=false`.
- **Fixed:** approval messages sent people to "My Approvals", a screen The Agent does not have. They now say **My Work**.
- **Fixed:** the Identity link in the Platform menu opened "Not Found".

## Running behind an HTTPS reverse proxy

- **Public addresses for each service** — with `USE_REVERSE_PROXY=true`, the new optional settings `AGENT_PUBLIC_BASE_URL`, `BUILDER_PUBLIC_BASE_URL` and `CC_PUBLIC_BASE_URL` are where users reach The Agent, the Builder and the Command Center. Their links back to AI Hub use `APP_PUBLIC_BASE_URL`. `.env.template` describes each setting and what the proxy must publish.
- **Workflow Designer Document steps** — behind a proxy the designer sends its Document step calls through the main app, signed in. The Document API itself is not published.
- **Fixed:** long chat turns in The Agent were cut by the proxy's idle timeout. The chat stream now sends a keepalive line every 15 seconds while a turn is working (`AGENT_CHAT_KEEPALIVE_SECONDS`, 0 = off).
- Installs without a reverse proxy set none of these and behave as before.

## Builder

- **Fixed:** users who opened the Builder from AI Hub were treated as not signed in.

## Automations

- **Fixed:** an automation on a connection whose password is kept in local secrets failed with "Neither DSN nor SERVER keyword supplied", while the connection's Test button passed.

## Sign-in

- **Remember Me** — ticking it also brings your user name back on the sign-in form in that browser. Only the user name is kept, never the password. Unticking the box and signing in forgets it.
- **Fixed:** in Identity & Security, a provider saved or deleted did not appear in the list until the page was reloaded.

## Upgrade notes

Run the v2.4.0 installer, restart the AI Hub services and hard-refresh your browser (Ctrl+Shift+R). No database migration is required.

No setting has to be added for an existing install. The reverse proxy addresses, the SAML provider and document attributes are all optional and off or empty until set.
