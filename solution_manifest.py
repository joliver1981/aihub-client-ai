"""
Solutions Gallery — manifest model.

Defines the shape of `solution.json` and `branding.json` inside a solution
bundle (.zip). The manifest is the source of truth for:
  - what's in the bundle (drives installer dispatch and UI preview)
  - what credentials must be prompted at install time
  - what post-install actions to offer the user

Kept as a plain dataclass so it serialises cleanly to JSON and is easy to
validate without pulling in Pydantic.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "solution.json"
BRANDING_FILENAME = "branding.json"
README_FILENAME = "README.md"
PREVIEW_DIR = "preview"

# Bundle top-level folders (each optional).
ASSET_FOLDERS = (
    "agents",
    "tools",
    "workflows",
    "integrations",
    "connections",
    "environments",
    "knowledge",
    "data",
)

# Placeholder pattern used in integrations / connections. Example: ${STRIPE_API_KEY}
PLACEHOLDER_RE = re.compile(r"\$\{([A-Z0-9_]{2,})\}")

# Semver-ish validation: N or N.M or N.M.P
_VERSION_RE = re.compile(r"^\d+(\.\d+){0,2}$")

# Solution IDs are used as folder names and part of URLs — keep them tight.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{1,63}$")


@dataclass
class CredentialPrompt:
    """A credential the install wizard will prompt for."""

    placeholder: str  # Uppercase token referenced in integration/connection files, e.g. STRIPE_API_KEY
    label: str        # Human-readable label shown in the wizard
    required: bool = True
    sample_value: str = ""  # When non-empty, user may click "Use sample" for demo mode
    description: str = ""   # Optional help text

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not self.placeholder or not re.match(r"^[A-Z0-9_]{2,}$", self.placeholder):
            errors.append(f"credential placeholder must be UPPER_SNAKE, got {self.placeholder!r}")
        if not self.label:
            errors.append(f"credential {self.placeholder}: label is required")
        return errors


# Supported post-install action types.
POST_INSTALL_TYPES = {"run_workflow", "chat_with_agent", "open_page"}


@dataclass
class PostInstallAction:
    type: str    # one of POST_INSTALL_TYPES
    target: str  # workflow name / agent name / URL path
    label: str

    def validate(self) -> List[str]:
        errors: List[str] = []
        if self.type not in POST_INSTALL_TYPES:
            errors.append(f"post_install type must be one of {sorted(POST_INSTALL_TYPES)}, got {self.type!r}")
        if not self.target:
            errors.append(f"post_install action ({self.type}): target is required")
        if not self.label:
            errors.append(f"post_install action ({self.type}): label is required")
        return errors


@dataclass
class AudienceModes:
    demo_data_included: bool = False
    consultant_rebrand_supported: bool = False


@dataclass
class SolutionAssets:
    """Inventory of what's in the bundle. The installer reads this to know
    which folders to process. Entries are filenames / subfolder names (no
    paths), relative to their asset-type folder inside the zip."""

    agents: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    workflows: List[str] = field(default_factory=list)
    integrations: List[str] = field(default_factory=list)
    connections: List[str] = field(default_factory=list)
    environments: List[str] = field(default_factory=list)
    knowledge: List[str] = field(default_factory=list)
    # Seed-data inventory is more complex — schema.sql presence, list of seed CSVs, sample input filenames.
    data: Dict[str, Any] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not any([
            self.agents, self.tools, self.workflows, self.integrations,
            self.connections, self.environments, self.knowledge, self.data,
        ])

    def count(self) -> int:
        """Total number of installable assets (used by the installer progress UI)."""
        return (
            len(self.agents) + len(self.tools) + len(self.workflows)
            + len(self.integrations) + len(self.connections)
            + len(self.environments) + len(self.knowledge)
            + (1 if self.data.get("schema_sql") else 0)
            + len(self.data.get("seeds", []) or [])
            + len(self.data.get("sample_inputs", []) or [])
        )


@dataclass
class SolutionManifest:
    """In-memory representation of solution.json."""

    id: str
    name: str
    version: str = "1.0.0"
    min_platform_version: str = "1.7"
    vertical: str = ""
    tags: List[str] = field(default_factory=list)
    description: str = ""
    author: str = ""
    homepage_url: str = ""
    assets: SolutionAssets = field(default_factory=SolutionAssets)
    credentials: List[CredentialPrompt] = field(default_factory=list)
    post_install: List[PostInstallAction] = field(default_factory=list)
    audience_modes: AudienceModes = field(default_factory=AudienceModes)
    # Free-form bag for future extension fields — preserved across round-trips.
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "min_platform_version": self.min_platform_version,
            "vertical": self.vertical,
            "tags": list(self.tags),
            "description": self.description,
            "author": self.author,
            "homepage_url": self.homepage_url,
            "assets": asdict(self.assets),
            "credentials": [asdict(c) for c in self.credentials],
            "post_install": [asdict(p) for p in self.post_install],
            "audience_modes": asdict(self.audience_modes),
        }
        if self.extra:
            d["extra"] = dict(self.extra)
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SolutionManifest":
        """Tolerant parser: missing keys → defaults; extra keys preserved in `extra`."""
        known = {
            "id", "name", "version", "min_platform_version", "vertical", "tags",
            "description", "author", "homepage_url", "assets", "credentials",
            "post_install", "audience_modes",
        }
        assets_raw = data.get("assets") or {}
        assets = SolutionAssets(
            agents=list(assets_raw.get("agents") or []),
            tools=list(assets_raw.get("tools") or []),
            workflows=list(assets_raw.get("workflows") or []),
            integrations=list(assets_raw.get("integrations") or []),
            connections=list(assets_raw.get("connections") or []),
            environments=list(assets_raw.get("environments") or []),
            knowledge=list(assets_raw.get("knowledge") or []),
            data=dict(assets_raw.get("data") or {}),
        )
        creds = [
            CredentialPrompt(
                placeholder=c.get("placeholder", ""),
                label=c.get("label", ""),
                required=bool(c.get("required", True)),
                sample_value=str(c.get("sample_value") or ""),
                description=str(c.get("description") or ""),
            )
            for c in (data.get("credentials") or [])
        ]
        actions = [
            PostInstallAction(
                type=str(p.get("type", "")),
                target=str(p.get("target", "")),
                label=str(p.get("label", "")),
            )
            for p in (data.get("post_install") or [])
        ]
        am_raw = data.get("audience_modes") or {}
        audience = AudienceModes(
            demo_data_included=bool(am_raw.get("demo_data_included", False)),
            consultant_rebrand_supported=bool(am_raw.get("consultant_rebrand_supported", False)),
        )
        extra = {k: v for k, v in data.items() if k not in known and k != "extra"}
        if "extra" in data and isinstance(data["extra"], dict):
            extra.update(data["extra"])
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            version=str(data.get("version") or "1.0.0"),
            min_platform_version=str(data.get("min_platform_version") or "1.7"),
            vertical=str(data.get("vertical") or ""),
            tags=list(data.get("tags") or []),
            description=str(data.get("description") or ""),
            author=str(data.get("author") or ""),
            homepage_url=str(data.get("homepage_url") or ""),
            assets=assets,
            credentials=creds,
            post_install=actions,
            audience_modes=audience,
            extra=extra,
        )

    @classmethod
    def from_json_file(cls, path: Path) -> "SolutionManifest":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    def validate(self) -> List[str]:
        """Return a list of human-readable validation errors. Empty = valid."""
        errors: List[str] = []

        if not self.id or not _ID_RE.match(self.id):
            errors.append(
                f"id must match {_ID_RE.pattern!r} (lowercase, digits, _ or -), got {self.id!r}"
            )
        if not self.name:
            errors.append("name is required")
        if not _VERSION_RE.match(self.version):
            errors.append(f"version must look like 1, 1.2, or 1.2.3 — got {self.version!r}")
        if self.min_platform_version and not _VERSION_RE.match(self.min_platform_version):
            errors.append(
                f"min_platform_version must look like 1.7, got {self.min_platform_version!r}"
            )

        # Cross-check: credentials referenced but no integrations/connections in assets is suspicious.
        if self.credentials and not (self.assets.integrations or self.assets.connections):
            # Warning rather than error — a consultant might declare credentials
            # for a knowledge source they add later. Keep this soft.
            logger.debug(
                "solution %s declares credentials but ships no integrations/connections",
                self.id,
            )

        for c in self.credentials:
            errors.extend(c.validate())
        for p in self.post_install:
            errors.extend(p.validate())

        # post_install targets should point to bundled assets when possible.
        asset_workflows = set(_basename(w) for w in self.assets.workflows)
        asset_agents = set(_basename(a) for a in self.assets.agents)
        for p in self.post_install:
            if p.type == "run_workflow" and p.target and _basename(p.target) not in asset_workflows:
                errors.append(
                    f"post_install run_workflow target '{p.target}' is not in assets.workflows"
                )
            if p.type == "chat_with_agent" and p.target and _basename(p.target) not in asset_agents:
                errors.append(
                    f"post_install chat_with_agent target '{p.target}' is not in assets.agents"
                )

        return errors


@dataclass
class BrandingOverrides:
    """Fields a consultant can override without repackaging solution guts.

    Read from branding.json if present. When missing, the manifest's own
    name/description are used.
    """

    display_name: str = ""
    tagline: str = ""
    logo_path: str = ""  # relative to preview/
    primary_color: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BrandingOverrides":
        return cls(
            display_name=str(data.get("display_name") or ""),
            tagline=str(data.get("tagline") or ""),
            logo_path=str(data.get("logo_path") or ""),
            primary_color=str(data.get("primary_color") or ""),
        )

    @classmethod
    def from_json_file(cls, path: Path) -> "BrandingOverrides":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def extract_placeholders(text: str) -> List[str]:
    """Return all unique ${PLACEHOLDER} tokens found in a string."""
    return sorted(set(PLACEHOLDER_RE.findall(text or "")))


def resolve_placeholders(text: str, values: Dict[str, str]) -> str:
    """Substitute ${PLACEHOLDER} tokens with provided values. Unknown tokens
    are left in place so the caller can decide how to handle them."""

    def _sub(match: re.Match) -> str:
        name = match.group(1)
        return values.get(name, match.group(0))

    return PLACEHOLDER_RE.sub(_sub, text or "")


def _basename(p: str) -> str:
    """Strip extension and any path component. 'workflows/foo.json' → 'foo'."""
    name = p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name
