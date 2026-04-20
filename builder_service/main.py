"""
Builder Service — Main Application
=====================================
FastAPI app that serves the builder agent UI and
provides the SSE chat endpoint backed by LangGraph.

Run with:
    uvicorn main:app --host 0.0.0.0 --port 8100 --reload
"""

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from builder_config import HOST, PORT, DEBUG, CORS_ORIGINS, print_service_urls
from graph.builder_graph import create_builder_graph
from services import SessionManager
from routes.chat import router as chat_router, init_routes
from routes.admin import router as admin_router
from routes.upload import router as upload_router
from execution import load_registries

# ─── Logging ──────────────────────────────────────────────────────────────

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-24s | %(levelname)-7s | %(message)s",
)
logger = logging.getLogger("builder_service")

# Quiet down noisy third-party loggers so our pipeline logs are readable
for noisy in ("httpx", "httpcore", "openai", "langchain", "langchain_core",
              "langchain_openai", "langgraph", "urllib3", "asyncio"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


# ─── Lifespan ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the graph and session manager at startup."""
    logger.info("Starting Builder Service...")

    # Print service URL configuration for debugging
    print_service_urls()

    # Load builder_agent registries (domain knowledge + action mappings)
    if load_registries():
        logger.info("Builder agent registries loaded")
    else:
        logger.warning("Builder agent registries failed to load - execution will be limited")

    # Create the LangGraph agent
    try:
        compiled_graph = create_builder_graph()
        logger.info("LangGraph builder agent initialized")
    except Exception as e:
        logger.error(f"Failed to create graph: {e}")
        compiled_graph = None

    # Create session manager with persistent storage
    data_dir = Path(__file__).parent / "data"
    session_mgr = SessionManager(data_dir=data_dir)

    # Wire into routes
    init_routes(compiled_graph, session_mgr)

    logger.info(f"Builder Service ready on {HOST}:{PORT}")
    yield
    logger.info("Builder Service shutting down")


# ─── App ──────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AI Hub Builder Agent",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(chat_router)
app.include_router(admin_router)
app.include_router(upload_router)

# Static files (frontend)
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def index():
    """Serve the main UI page."""
    return FileResponse(os.path.join(static_dir, "index.html"))


@app.get("/admin")
async def admin():
    """Serve the admin configuration page."""
    return FileResponse(os.path.join(static_dir, "admin.html"))


@app.get("/health")
async def health_root():
    """Health check at root path (mirrors /api/health for load balancers)."""
    from routes.chat import health
    return await health()


# ─── Run ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import uvicorn

    # Detect if running as a PyInstaller-frozen executable.
    # In frozen mode we MUST:
    #   1. Pass the app object directly (not a "module:attr" string) so
    #      uvicorn doesn't try to re-import the module — which would
    #      re-execute the .exe and cause an infinite fork-bomb.
    #   2. Disable reload — the reloader spawns a child process that
    #      re-runs the exe, again causing infinite spawning.
    is_frozen = getattr(sys, 'frozen', False)

    if is_frozen:
        uvicorn.run(app, host=HOST, port=PORT, reload=False)
    else:
        uvicorn.run("main:app", host=HOST, port=PORT, reload=DEBUG)
