"""Freshness check: is any dist\ tree older than the source it is built from?

Run at the end of a build (v4 step [17]). Exit 1 if anything is stale, so a
build that silently shipped an old artifact is loud instead.

Why this exists: Build_AIHub_Executables_OneDir_Dev_v3.bat never built The
Agent, so dist\agent_service kept whatever was last staged by hand. On
2026-09-06 that shipped an installer two days behind the tree (no
search_tables, no delete_skill) and the gap was only found by probing the
installed box's live tool surface.
"""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DIST = REPO / "dist"

# dist tree -> source dirs whose newest file it must not be older than.
TARGETS = {
    "app":                    ["app.py", "routes", "templates", "static"],
    "document_api_server":    ["wsgi_doc_api.py", "DocUtils.py"],
    "document_job_processor": ["app_doc_job_q.py"],
    "job_scheduler_service":  ["app_jss_main.py"],
    "wsgi_vector_api":        ["wsgi_vector_api.py"],
    "wsgi_agent_api":         ["wsgi_agent_api.py"],
    "wsgi_knowledge_api":     ["wsgi_knowledge_api.py"],
    "wsgi_executor_service":  ["wsgi_executor_service.py"],
    "mcp_gateway":            ["builder_mcp"],
    "builder_service":        ["builder_service"],
    "builder_data":           ["builder_data"],
    "cloud_gateway":          ["builder_cloud"],
    "command_center_service": ["command_center_service", "command_center"],
    "browser_use_service":    ["browser_use_service"],
    "agent_service":          ["agent_service"],
}

SKIP_DIRS = {"__pycache__", ".git", "node_modules", "logs", "data", ".pytest_cache"}
SKIP_EXT = {".pyc", ".log", ".db", ".db-wal", ".db-shm"}


def newest(path: Path) -> float:
    """Newest mtime under path (file or dir), ignoring build noise."""
    if path.is_file():
        return path.stat().st_mtime
    newest_t = 0.0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if Path(f).suffix.lower() in SKIP_EXT:
                continue
            try:
                t = (Path(root) / f).stat().st_mtime
            except OSError:
                continue
            if t > newest_t:
                newest_t = t
    return newest_t


def stamp(t: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(t).strftime("%m-%d %H:%M") if t else "missing"


def main() -> int:
    stale, missing, ok = [], [], []
    for tree, sources in TARGETS.items():
        dist_path = DIST / tree
        if not dist_path.exists():
            missing.append(tree)
            continue
        d_t = newest(dist_path)
        s_t = max((newest(REPO / s) for s in sources if (REPO / s).exists()), default=0.0)
        (stale if s_t > d_t else ok).append((tree, d_t, s_t))

    print(f"{'dist tree':26s} {'built':12s} {'newest source':12s}")
    print("-" * 54)
    for tree, d_t, s_t in sorted(ok):
        print(f"  {tree:24s} {stamp(d_t):12s} {stamp(s_t):12s} ok")
    for tree, d_t, s_t in sorted(stale):
        print(f"! {tree:24s} {stamp(d_t):12s} {stamp(s_t):12s} STALE")
    for tree in sorted(missing):
        print(f"? {tree:24s} {'--':12s} {'--':12s} NOT BUILT")

    if stale or missing:
        print()
        if stale:
            print(f"STALE: {', '.join(t for t, _, _ in stale)}")
        if missing:
            print(f"NOT BUILT: {', '.join(missing)}")
        return 1
    print("\nAll dist trees are newer than their sources.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
