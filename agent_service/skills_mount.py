"""
Skills — procedural memory with scopes (A3).

Four scopes under data/agent/ (plan §4, James's Round-3/8 decisions):
  skills/product/<name>/SKILL.md   shipped with the platform (read-only intent)
  skills/tenant/<name>/SKILL.md    learned here; admin-approved; exportable
  groups/<gid>/skills/<name>/      shared with a group (user confirm only)
  users/<uid>/skills/<name>/       private

A session mounts product + tenant + the union of the user's groups + their own
scope: build_user_workspace() assembles data/agent/users/<uid>/ws/.claude/skills
by copying the SKILL.md trees (small text files; sync per turn is cheap), and
the SDK loads them via setting_sources=["project"] + skills="all" with cwd=ws.

Skills carry procedure and gotchas but must lean on discovery tools for
current facts — the save tool's description enforces the drift guardrail.
"""

import os
import re
import shutil
from typing import Optional

from agent_config import (
    DATA_DIR, SKILLS_PRODUCT_DIR, SKILLS_TENANT_DIR, USERS_DIR, GROUPS_DIR,
    logger,
)

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,63}$")


def _scope_dir(scope: str, user_id: int = 0, group_id: int = 0) -> Optional[str]:
    if scope == "product":
        return SKILLS_PRODUCT_DIR
    if scope == "tenant":
        return SKILLS_TENANT_DIR
    if scope == "group" and group_id:
        return os.path.join(GROUPS_DIR, str(int(group_id)), "skills")
    if scope == "user" and user_id:
        return os.path.join(USERS_DIR, str(int(user_id)), "skills")
    return None


def valid_name(name: str) -> bool:
    return bool(_NAME_RE.match(name or ""))


def write_skill(scope: str, name: str, description: str, content: str,
                user_id: int = 0, group_id: int = 0) -> str:
    """Write <scope>/<name>/SKILL.md with frontmatter. Returns the path."""
    if not valid_name(name):
        raise ValueError("skill name must be kebab-case (a-z, 0-9, '-'), 2-64 chars")
    base = _scope_dir(scope, user_id, group_id)
    if not base:
        raise ValueError(f"invalid scope '{scope}'")
    d = os.path.join(base, name)
    os.makedirs(d, exist_ok=True)
    desc = " ".join(str(description or "").split())[:500]
    body = (f"---\nname: {name}\ndescription: {desc}\n---\n\n"
            f"{str(content or '').strip()}\n")
    path = os.path.join(d, "SKILL.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    logger.info(f"skill written: {scope}/{name} ({len(body)} bytes)")
    return path


def delete_skill(scope: str, name: str, user_id: int = 0, group_id: int = 0) -> bool:
    base = _scope_dir(scope, user_id, group_id)
    if not base or not valid_name(name):
        return False
    d = os.path.join(base, name)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
        logger.info(f"skill deleted: {scope}/{name}")
        return True
    return False


def _read_meta(skill_dir: str) -> dict:
    path = os.path.join(skill_dir, "SKILL.md")
    meta = {"description": "", "size": 0}
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        meta["size"] = len(text)
        m = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
        if m:
            meta["description"] = m.group(1).strip()
    except Exception:
        pass
    return meta


def list_skills(user_id: int = 0, group_ids: Optional[list] = None) -> list:
    """Inventory across scopes (admin view + the list_skills tool)."""
    out = []

    def scan(scope, base, gid=None):
        if not os.path.isdir(base):
            return
        for name in sorted(os.listdir(base)):
            d = os.path.join(base, name)
            if os.path.isfile(os.path.join(d, "SKILL.md")):
                out.append({"scope": scope, "group_id": gid, "name": name,
                            **_read_meta(d)})

    scan("product", SKILLS_PRODUCT_DIR)
    scan("tenant", SKILLS_TENANT_DIR)
    for gid in (group_ids or []):
        scan("group", os.path.join(GROUPS_DIR, str(int(gid)), "skills"), gid)
    if user_id:
        scan("user", os.path.join(USERS_DIR, str(int(user_id)), "skills"))
    return out


def read_skill(scope: str, name: str, user_id: int = 0, group_id: int = 0) -> str:
    base = _scope_dir(scope, user_id, group_id)
    if not base or not valid_name(name):
        return ""
    try:
        with open(os.path.join(base, name, "SKILL.md"), "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def build_user_workspace(user_id: int, group_ids: Optional[list] = None) -> str:
    """Assemble the per-user session workspace: ws/.claude/skills = product +
    tenant + user's groups + user's own. Fresh copy each call (files are tiny;
    a stale mount would be worse than the copy cost)."""
    ws = os.path.join(USERS_DIR, str(int(user_id or 0)), "ws")
    skills_dst = os.path.join(ws, ".claude", "skills")
    shutil.rmtree(skills_dst, ignore_errors=True)
    os.makedirs(skills_dst, exist_ok=True)

    def mount(base, prefix=""):
        if not os.path.isdir(base):
            return
        for name in os.listdir(base):
            src = os.path.join(base, name)
            if os.path.isfile(os.path.join(src, "SKILL.md")):
                # Later scopes win name collisions: user > group > tenant > product
                dst = os.path.join(skills_dst, name)
                shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(src, dst)

    mount(SKILLS_PRODUCT_DIR)
    mount(SKILLS_TENANT_DIR)
    for gid in (group_ids or []):
        mount(os.path.join(GROUPS_DIR, str(int(gid)), "skills"))
    if user_id:
        mount(os.path.join(USERS_DIR, str(int(user_id)), "skills"))
    return ws
