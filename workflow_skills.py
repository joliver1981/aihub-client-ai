# workflow_skills.py
"""Skills for the workflow AI nodes (James, 2026-09-22).

A skill is a SKILL.md file that The Agent already keeps under
data/agent/skills/ — `product` (shipped with the platform) and `tenant`
(learned here, admin-approved). The AI Extract and AI Action nodes can now name
one (`skillName`) and/or carry the skill text inline (`skillText`); the text is
injected into the node's prompt as a SKILL GUIDANCE block.

Backward compatibility is the contract of this module:
  * both keys are OPTIONAL and absent on every pre-existing workflow — with
    neither set, resolve_node_skill() returns '' and the callers leave their
    prompts exactly as they were;
  * only tenant and product scopes are selectable — a workflow is a shared
    asset, so what it does must not depend on who happens to run it (user and
    group skills are per-person and stay with The Agent);
  * a named skill that cannot be found FAILS the node with a clear message
    rather than silently extracting without it.

The skill files are read fresh on every execution (no cache): editing a
tenant skill changes the next run, exactly like editing the node itself.
"""
import os
import re
from typing import Dict, List, Optional, Tuple

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,63}$")   # same rule as agent_service/skills_mount.py
SELECTABLE_SCOPES = ("tenant", "product")              # tenant wins a name collision (as in The Agent)
MAX_SKILL_CHARS = 40_000


def valid_name(name: str) -> bool:
    return bool(_NAME_RE.match(name or ""))


def skills_root() -> str:
    """data/agent/skills under the app root (frozen-aware via CommonUtils)."""
    override = os.getenv("WORKFLOW_SKILLS_ROOT")
    if override:
        return override
    from CommonUtils import get_app_path
    return get_app_path("data", "agent", "skills")


def split_frontmatter(text: str) -> Tuple[str, str]:
    """Return (description, body) for a SKILL.md. The frontmatter is the
    leading '---' block; `description:` may continue on indented lines."""
    text = text or ""
    if not text.lstrip().startswith("---"):
        return "", text.strip()
    lines = text.lstrip().splitlines()
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return "", text.strip()
    desc_lines: List[str] = []
    in_desc = False
    for line in lines[1:end]:
        if re.match(r"^description:\s*", line):
            in_desc = True
            desc_lines.append(re.sub(r"^description:\s*", "", line).strip())
        elif in_desc and (line.startswith(" ") or line.startswith("\t")):
            desc_lines.append(line.strip())
        else:
            in_desc = False
    description = " ".join(d for d in desc_lines if d).strip().strip('"').strip("'")
    body = "\n".join(lines[end + 1:]).strip()
    return description, body


def _read_file(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def list_skills() -> List[Dict]:
    """Skills a workflow node may pick: tenant + product, one entry per name
    (tenant overrides product), sorted by name. Each: scope/name/description/size."""
    root = skills_root()
    found: Dict[str, Dict] = {}
    for scope in ("product", "tenant"):          # tenant scanned last → wins collisions
        base = os.path.join(root, scope)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            if not valid_name(name):
                continue
            path = os.path.join(base, name, "SKILL.md")
            if not os.path.isfile(path):
                continue
            text = _read_file(path)
            if text is None:
                continue
            description, body = split_frontmatter(text)
            found[name] = {"scope": scope, "name": name, "description": description,
                           "size": len(body)}
    return [found[k] for k in sorted(found)]


def read_skill(name: str) -> Optional[Dict]:
    """The named skill (tenant first, then product) as
    {scope, name, description, content} with the frontmatter stripped, or None."""
    if not valid_name(name):
        return None
    root = skills_root()
    for scope in SELECTABLE_SCOPES:
        path = os.path.join(root, scope, name, "SKILL.md")
        if os.path.isfile(path):
            text = _read_file(path)
            if text is None:
                continue
            description, body = split_frontmatter(text)
            return {"scope": scope, "name": name, "description": description, "content": body}
    return None


def resolve_node_skill(config: Optional[Dict]) -> str:
    """The skill text for a node config: the named skill's body (if `skillName`)
    followed by the inline `skillText`. '' when the node uses neither — the
    case for every workflow saved before this feature existed."""
    config = config or {}
    name = str(config.get("skillName") or "").strip()
    inline = str(config.get("skillText") or "").strip()
    parts: List[str] = []
    if name:
        if not valid_name(name):
            raise ValueError(f"Skill name '{name}' is not valid (kebab-case: a-z, 0-9, '-')")
        skill = read_skill(name)
        if not skill:
            raise ValueError(
                f"Skill '{name}' was not found in the tenant or product skills "
                f"({skills_root()}). Pick it again in the node, or clear the skill.")
        if skill["content"]:
            parts.append(skill["content"])
    if inline:
        parts.append(inline)
    text = "\n\n".join(parts).strip()
    if len(text) > MAX_SKILL_CHARS:
        raise ValueError(
            f"Skill text is {len(text):,} characters; the limit is {MAX_SKILL_CHARS:,}. "
            f"Shorten the skill or the inline text.")
    return text


def describe_node_skill(config: Optional[Dict]) -> str:
    """Short human label for logs: which skill / inline text a node applies."""
    config = config or {}
    name = str(config.get("skillName") or "").strip()
    inline = str(config.get("skillText") or "").strip()
    bits = []
    if name:
        bits.append(f"skill '{name}'")
    if inline:
        bits.append("embedded text")
    return " + ".join(bits) if bits else "none"
