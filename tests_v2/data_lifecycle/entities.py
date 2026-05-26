"""Entity specifications for the data-lifecycle CRUD suite.

Each ``EntitySpec`` is a recipe for one round-trip:
    create -> read -> list -> update -> delete -> read 404

The spec is data, not code, so adding a new entity is just appending a
new dataclass instance to ``ENTITIES`` (or to ``QUIRKY_ENTITIES`` if the
entity needs a dedicated module).

URL templates use ``{id}`` and ``{parent_id}`` placeholders that are
filled in at runtime via ``str.format``. URLs are paths (no leading
host) so the test module can prepend the base URL.

Body builders are callables that receive a ``name`` string (and, for
nested entities, a ``parent_id``) and return the JSON body for create
or update. Returning ``None`` from ``update_body`` signals that the
entity has no real update endpoint and the update/persist tests should
be skipped (not xfailed).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# Spec dataclass
# ---------------------------------------------------------------------------

@dataclass
class EntitySpec:
    """One CRUD recipe."""

    # Display name used in pytest ids and the README matrix.
    name: str

    # ---- URL templates (paths only, no host). ----
    # POST here to create. May contain ``{parent_id}`` for nested entities.
    create_url: str
    # GET here to read one. Contains ``{id}``.
    get_url: str
    # GET here to list. May contain ``{parent_id}``.
    list_url: str
    # PUT here to update (or None if entity has no update endpoint).
    update_url: Optional[str]
    # DELETE here to delete. Contains ``{id}``.
    delete_url: str

    # ---- HTTP methods (defaults are POST/GET/GET/PUT/DELETE). ----
    create_method: str = "POST"
    update_method: str = "PUT"
    delete_method: str = "DELETE"

    # ---- Body builders. ----
    # Both take a dict of substitutions (at minimum ``name``) and return
    # the JSON body. For nested entities the parent fixture sets
    # ``parent_id`` in the substitutions dict.
    create_body: Callable[[Dict[str, Any]], Dict[str, Any]] = field(
        default=lambda subs: {"name": subs["name"]}
    )
    # Return ``None`` from update_body to skip update tests for this entity.
    update_body: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None

    # ---- Response shape. ----
    # JSON key (or list of fallback keys) containing the new entity id on
    # create.  Searched in order.
    id_keys: List[str] = field(default_factory=lambda: ["id"])
    # If the create response wraps the id under a sub-object key, look
    # there first (e.g. for /api/integrations the id sits at the top
    # level but for some endpoints it's under "data").
    id_wrapper_keys: List[str] = field(default_factory=list)

    # Which key in the list response holds the array of rows. ``""``
    # means the response IS the list. Otherwise list_payload[key] is
    # the list.
    list_key: str = ""

    # Which key on each list row holds the row's id.
    list_id_key: str = "id"
    # Which key on each list row holds the human-readable name (used
    # for filtering DLT_v2_ leftovers).
    list_name_key: str = "name"

    # Which key on the get response holds the human-readable name.
    get_name_key: str = "name"

    # Whether the list endpoint requires a parent fixture (e.g. sets are
    # listed per-retailer).
    list_needs_parent: bool = False

    # Status code the create endpoint actually returns on success.
    # Some endpoints return 200, others 201.
    create_success_codes: List[int] = field(default_factory=lambda: [200, 201])

    # If the list endpoint returns a JSON-encoded string (the legacy
    # ``dataframe_to_json`` pattern), set this so the runner double-decodes.
    list_returns_json_string: bool = False

    # If delete is by POST with a body instead of DELETE on a URL with
    # the id baked in, set ``delete_body`` and the runner will POST.
    delete_body: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None

    # Free-form notes that show up in the README.
    notes: str = ""


# ---------------------------------------------------------------------------
# Body builders
# ---------------------------------------------------------------------------

def _retailer_create(subs):
    return {"name": subs["name"], "notes": "DLT v2 lifecycle test"}


def _retailer_update(subs):
    return {"name": subs["name"], "notes": "DLT v2 lifecycle test - updated"}


def _set_create(subs):
    return {
        "category": subs["name"],
        "description": "DLT v2 lifecycle test set",
    }


def _set_update(subs):
    return {"description": "DLT v2 lifecycle test set - updated"}


def _schema_create(subs):
    return {
        "name": subs["name"],
        "description": "DLT v2 lifecycle test schema",
        "fields": [
            {
                "name": "field_one",
                "type": "string",
                "description": "first field",
                "required": False,
            }
        ],
    }


def _schema_update(subs):
    return {
        "name": subs["name"],
        "description": "DLT v2 lifecycle test schema - updated",
    }


def _agent_create(subs):
    return {
        "agent_id": 0,
        "agent_description": subs["name"],
        "agent_objective": (
            f"You are a helpful AI assistant named {subs['name']}. "
            "Created by the DLT v2 lifecycle test suite."
        ),
        "agent_enabled": True,
        "tool_names": [],
        "core_tool_names": [],
    }


def _agent_update(subs):
    # /add/agent doubles as update when agent_id != 0
    return {
        "agent_id": subs["id"],
        "agent_description": subs["name"],
        "agent_objective": (
            f"Updated objective for {subs['name']} - DLT v2 lifecycle."
        ),
        "agent_enabled": True,
        "tool_names": [],
        "core_tool_names": [],
    }


def _agent_delete(subs):
    return {"agent_id": int(subs["id"])}


def _connection_create(subs):
    # /add/connection takes connection_id=0 for new
    return {
        "connection_id": 0,
        "connection_name": subs["name"],
        "server": "localhost",
        "port": 1433,
        "database_name": "test_db",
        "database_type": "SQL Server",
        "user_name": "dlt_test",
        "password": "DltTestPassword123",
        "parameters": "",
        "connection_string": "",
        "odbc_driver": "",
        "instance_url": "",
        "token": "",
        "api_key": "",
        "dsn": "",
    }


def _connection_update(subs):
    return {
        "connection_id": int(subs["id"]),
        "connection_name": subs["name"],
        "server": "localhost",
        "port": 1433,
        "database_name": "test_db_updated",
        "database_type": "SQL Server",
        "user_name": "dlt_test",
        "password": "••••••••",  # unchanged sentinel
        "parameters": "",
        "connection_string": "",
        "odbc_driver": "",
    }


def _integration_create(subs):
    # custom_rest_api is the simplest universally-shipped template that
    # doesn't require external creds to *create* (it'll fail on first
    # call, but creation is what we test here).
    return {
        "template_key": subs.get("template_key", "custom_rest_api"),
        "integration_name": subs["name"],
        "description": "DLT v2 lifecycle test integration",
        "instance_config": {"base_url": "https://example.invalid"},
        "credentials": {"api_key": "dlt-fake-key"},
    }


def _integration_update(subs):
    return {
        "integration_name": subs["name"],
        "description": "DLT v2 lifecycle test integration - updated",
    }


def _mcp_create(subs):
    return {
        "server_name": subs["name"],
        "server_type": "local",
        "command": "echo",
        "args": ["hello"],
        "env_vars": {},
        "description": "DLT v2 lifecycle test MCP server",
        "category": "Test",
    }


def _mcp_update(subs):
    return {
        "server_name": subs["name"],
        "server_type": "local",
        "command": "echo",
        "args": ["hello-updated"],
        "env_vars": {},
        "description": "DLT v2 lifecycle test MCP server - updated",
        "category": "Test",
    }


def _user_create(subs):
    return {
        "user_id": 0,
        "user_name": subs["name"].lower(),
        "role": 1,
        "name": subs["name"],
        "email": f"{subs['name'].lower()}@dlt.test",
        "phone": "",
        "password": "DltTestPassword123!",
    }


def _user_update(subs):
    return {
        "user_id": int(subs["id"]),
        "user_name": subs["name"].lower(),
        "role": 1,
        "name": subs["name"] + " (updated)",
        "email": f"{subs['name'].lower()}@dlt.test",
        "phone": "",
        "password": "",  # blank == don't change
    }


def _user_delete(subs):
    return {"user_id": int(subs["id"])}


def _identity_provider_create(subs):
    return {
        "id": 0,
        "provider_type": "local",
        "provider_name": subs["name"],
        "is_enabled": False,
        "is_default": False,
        "auto_provision": False,
        "default_role": 1,
        "config": {},
        "group_role_mapping": {},
    }


def _identity_provider_update(subs):
    return {
        "id": int(subs["id"]),
        "provider_type": "local",
        "provider_name": subs["name"],
        "is_enabled": False,
        "is_default": False,
        "auto_provision": False,
        "default_role": 1,
        "config": {"updated": True},
        "group_role_mapping": {},
    }


# ---------------------------------------------------------------------------
# Entity catalog
# ---------------------------------------------------------------------------

ENTITIES: List[EntitySpec] = [
    EntitySpec(
        name="compliance_retailer",
        create_url="/api/compliance/retailers",
        get_url="/api/compliance/retailers/{id}",
        list_url="/api/compliance/retailers",
        update_url="/api/compliance/retailers/{id}",
        delete_url="/api/compliance/retailers/{id}",
        create_body=_retailer_create,
        update_body=_retailer_update,
        id_keys=["retailer_id"],
        list_key="retailers",
        list_id_key="retailer_id",
        list_name_key="name",
        get_name_key="name",
        create_success_codes=[200, 201],
    ),
    EntitySpec(
        name="compliance_schema",
        create_url="/api/compliance/schemas",
        get_url="/api/compliance/schemas/{id}",
        list_url="/api/compliance/schemas",
        update_url="/api/compliance/schemas/{id}",
        delete_url="/api/compliance/schemas/{id}",
        create_body=_schema_create,
        update_body=_schema_update,
        id_keys=["schema_id"],
        list_key="schemas",
        list_id_key="schema_id",
        list_name_key="name",
        get_name_key="name",
        create_success_codes=[200, 201],
    ),
    EntitySpec(
        name="agent",
        create_url="/add/agent",
        get_url="/get/agent_info",          # see test for filter logic
        list_url="/get/agent_info",
        update_url="/add/agent",            # same endpoint, agent_id != 0
        update_method="POST",
        delete_url="/delete/agent",
        delete_method="POST",
        create_body=_agent_create,
        update_body=_agent_update,
        delete_body=_agent_delete,
        id_keys=["message"],                # /add/agent returns {"status":"success","message": <id>}
        list_key="",                        # list endpoint returns array directly
        list_id_key="id",
        list_name_key="description",
        get_name_key="description",
        create_success_codes=[200],
    ),
    EntitySpec(
        name="connection",
        create_url="/add/connection",
        get_url=None,                       # no single-row GET endpoint; use list filter
        list_url="/get/connections",
        update_url="/add/connection",
        update_method="POST",
        delete_url="/delete/connection/{id}",
        delete_method="POST",
        create_body=_connection_create,
        update_body=_connection_update,
        id_keys=["response"],               # /add/connection returns {"status":"success","response": str(new_id)}
        list_key="",
        list_id_key="id",
        list_name_key="connection_name",
        list_returns_json_string=True,
        create_success_codes=[200],
        notes="No single-row GET; read uses list filter by name",
    ),
    EntitySpec(
        name="integration",
        create_url="/api/integrations",
        get_url="/api/integrations/{id}",
        list_url="/api/integrations",
        update_url="/api/integrations/{id}",
        delete_url="/api/integrations/{id}",
        create_body=_integration_create,
        update_body=_integration_update,
        id_keys=["integration_id"],
        list_key="integrations",
        list_id_key="integration_id",
        list_name_key="integration_name",
        get_name_key="integration_name",
        create_success_codes=[200, 201],
        notes="Requires a template_key resolvable on this install",
    ),
    EntitySpec(
        name="mcp_server",
        create_url="/api/mcp/servers",
        get_url="/api/mcp/servers/{id}",
        list_url="/api/mcp/servers",
        update_url="/api/mcp/servers/{id}",
        delete_url="/api/mcp/servers/{id}",
        create_body=_mcp_create,
        update_body=_mcp_update,
        id_keys=["server_id"],
        list_key="",                        # returns array
        list_id_key="server_id",
        list_name_key="server_name",
        get_name_key="server_name",
        create_success_codes=[200, 201],
    ),
    EntitySpec(
        name="user",
        create_url="/add/user",
        get_url="/get/user/{id}",
        list_url="/get/users",
        update_url="/add/user",
        update_method="POST",
        delete_url="/delete/user",
        delete_method="POST",
        create_body=_user_create,
        update_body=_user_update,
        delete_body=_user_delete,
        id_keys=["response"],
        list_key="",
        list_id_key="id",
        list_name_key="user_name",
        get_name_key="user_name",
        list_returns_json_string=True,
        create_success_codes=[200],
        notes="Requires admin role (>=3); /get/user returns JSON-string-of-list",
    ),
    EntitySpec(
        name="identity_provider",
        create_url="/api/identity/providers",
        get_url=None,                       # no single-row GET
        list_url="/api/identity/providers",
        update_url="/api/identity/providers",  # save endpoint is upsert-by-id
        update_method="POST",
        delete_url="/api/identity/providers/{id}",
        create_body=_identity_provider_create,
        update_body=_identity_provider_update,
        id_keys=[],                         # save endpoint does NOT return id - known gap
        list_key="providers",
        list_id_key="id",
        list_name_key="provider_name",
        create_success_codes=[200],
        notes=(
            "Save endpoint returns no id. Lookup is done by listing and "
            "filtering on provider_name."
        ),
    ),
]


# Quirky entities documented separately (no automatic lifecycle):
QUIRKY_ENTITIES = {
    "workflow": "Lifecycle covered in test_workflow_lifecycle.py (custom JSON shape)",
    "compliance_set": (
        "Nested under retailer; covered in test_compliance_set_lifecycle.py"
    ),
    "custom_tool": (
        "Save endpoint returns HTML (render_template), not JSON. "
        "No clean CRUD test possible without scraping; out of scope."
    ),
    "knowledge_entry": (
        "Add endpoint requires multipart file upload (POST /add/agent_knowledge). "
        "Covered by separate file-upload tests; out of scope here."
    ),
    "solution": (
        "/api/solutions/catalog requires login_required (not api key); "
        "out of scope for API-key-only CRUD suite."
    ),
    "document_type": (
        "Document types are not a CRUD entity in this codebase; document "
        "uploads create rows in Documents table via processor jobs."
    ),
}
