"""
Builder Service — Permission Checks
=====================================
Maps user roles to permitted domain operations.
Used for both LLM prompt injection and hard execution checks.

Role levels match role_decorators.py in the main Flask app:
    1 = User (basic access)
    2 = Developer (connections, monitoring, integrations)
    3 = Admin (user management, groups, permissions)
"""

from typing import Optional

# Role constants matching role_decorators.py
ROLE_USER = 1
ROLE_DEVELOPER = 2
ROLE_ADMIN = 3

ROLE_NAMES = {
    ROLE_USER: "User",
    ROLE_DEVELOPER: "Developer",
    ROLE_ADMIN: "Admin",
}

# Domain → minimum role required (None = any authenticated user)
DOMAIN_ROLE_REQUIREMENTS = {
    "agents": None,
    "workflows": None,
    "documents": None,
    "tools": None,
    "knowledge": None,
    "email": None,
    "connections": ROLE_DEVELOPER,
    "integrations": ROLE_DEVELOPER,
    "environments": ROLE_DEVELOPER,
    "jobs": ROLE_DEVELOPER,
    "schedules": ROLE_DEVELOPER,
    "mcp": ROLE_DEVELOPER,
    "users": ROLE_ADMIN,
}


def get_user_role(user_context: Optional[dict]) -> int:
    """Extract role from user_context dict, default to User.

    Defensive: some callers may pass `{"role": None}`.
    """
    if not user_context:
        return ROLE_USER

    role = user_context.get("role", ROLE_USER)
    if role is None:
        return ROLE_USER

    try:
        return int(role)
    except Exception:
        return ROLE_USER


def get_user_display_name(user_context: Optional[dict]) -> str:
    """Get display name from user context."""
    if not user_context:
        return "User"
    return user_context.get("name") or user_context.get("username") or "User"


def get_role_name(role: int) -> str:
    """Get human-readable name for a role level."""
    return ROLE_NAMES.get(role, "User")


def can_access_capability(user_role: int, required_role: Optional[int]) -> bool:
    """Check if a user's role meets the minimum requirement."""
    if required_role is None:
        return True
    return user_role >= required_role


def get_permission_context_for_prompt(user_context: Optional[dict]) -> str:
    """
    Generate a permission context string to inject into the LLM system prompt.
    This tells the LLM what the user can and cannot do based on their role.
    """
    role = get_user_role(user_context)
    name = get_user_display_name(user_context)
    role_name = get_role_name(role)

    lines = [
        f"CURRENT USER: {name} (Role: {role_name})",
        "",
        "PERMISSION RULES:",
    ]

    if role >= ROLE_ADMIN:
        lines.append(
            "- You have FULL ACCESS to all platform features including "
            "user management, group management, and all administrative functions."
        )
    elif role >= ROLE_DEVELOPER:
        lines.extend([
            "- You can manage: agents, workflows, tools, documents, knowledge, "
            "connections, integrations, jobs, schedules, environments, MCP servers, and email.",
            "- You CANNOT manage users or groups (requires Admin role).",
            "- If asked about user/group management, explain that Admin access is required "
            "and suggest contacting an administrator.",
        ])
    else:
        lines.extend([
            "- You can manage: agents, workflows, tools, documents, and knowledge.",
            "- You CANNOT manage: connections, integrations, jobs, schedules, "
            "environments, MCP servers, users, or groups.",
            "- If asked about restricted features, explain which role is required "
            "(Developer for connections/integrations/schedules, Admin for user management).",
        ])

    lines.extend([
        "",
        "IMPORTANT: Never plan or attempt actions the user's role does not permit. "
        "Instead, explain what role is required and suggest they contact an administrator.",
    ])

    return "\n".join(lines)
