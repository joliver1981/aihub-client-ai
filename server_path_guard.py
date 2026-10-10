"""Guard for the main app's server-file routes (2026-10-10).

/workflow/file/{read,write,append,check,delete} and /folder/{list_files,info}
act on any path the caller names. They had no role check, so any signed-in
user — and, with AUTH_MIDDLEWARE_DRY_RUN=true, anyone — could read, write or
delete any file on the server. They now require a Developer session or the
internal API key (the same bar as The Agent's server-file tools), and, like
those tools, they refuse the platform's secret store, the OS system folder
and .env files, and refuse a delete that would take any of those with it.

Developers can already run code on the server (automations, code flows), so
the deny list is defense-in-depth, not a sandbox.
"""
import os
from typing import Optional

_PROTECTED = ("This location is protected (the platform's secret store or the system folder) "
              "and cannot be accessed through this route.")
_ENV_FILE = "Environment files (.env) hold credentials and cannot be accessed through this route."


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def protected_dirs() -> set:
    """Folders these routes never touch: the secret store (where this install
    keeps it) and the OS system folder."""
    app_root = os.getenv("APP_ROOT") or os.path.dirname(os.path.abspath(__file__))
    dirs = [os.path.join(app_root, "data", "secrets"),
            os.environ.get("SystemRoot", r"C:\Windows")]
    try:  # the running manager's own folder, when it has been created
        import local_secrets
        mgr = getattr(local_secrets, "_secrets_manager", None)
        if mgr is not None:
            dirs.append(str(mgr.secrets_dir))
    except Exception:
        pass
    return {_norm(d) for d in dirs if d}


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def refusal(path, for_delete: bool = False) -> Optional[str]:
    """Why `path` may not be used through the server-file routes, or None.
    for_delete also refuses a folder that CONTAINS a protected one (a
    recursive delete would take it along)."""
    if not isinstance(path, str) or not path.strip():
        return "A file or folder path is required."
    target = _norm(path.strip())
    name = os.path.basename(target)
    if name == ".env" or name.startswith(".env."):
        return _ENV_FILE
    for folder in protected_dirs():
        if _inside(target, folder):
            return _PROTECTED
        if for_delete and _inside(folder, target):
            return ("Deleting this would remove protected files (the platform's secret store or "
                    "the system folder), so it is refused.")
    return None
