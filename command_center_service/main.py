"""
Command Center Service — Main Application
=============================================
FastAPI app that serves the Command Center UI and provides
the SSE chat endpoint backed by LangGraph.

Run with:
    uvicorn main:app --host 0.0.0.0 --port 5091 --reload
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from cc_config import HOST, PORT, DEBUG, CORS_ORIGINS, print_service_urls
from services import SessionManager
from routes.health import router as health_router, init_health

# ─── Logging ──────────────────────────────────────────────────────────────

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')

# Log to both terminal + rotating file under command_center_service/data/logs/
_log_dir = Path(__file__).parent / "data" / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_log_file = _log_dir / "command_center_service.log"

_max_bytes = int(os.getenv("CC_LOG_MAX_BYTES", str(10 * 1024 * 1024)))  # 10MB
_backup_count = int(os.getenv("CC_LOG_BACKUP_COUNT", "5"))

_file_handler = RotatingFileHandler(
    filename=str(_log_file),
    maxBytes=_max_bytes,
    backupCount=_backup_count,
    encoding="utf-8",
)
_stream_handler = logging.StreamHandler(sys.stdout)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-24s | %(levelname)-7s | %(message)s",
    handlers=[_stream_handler, _file_handler],
)
logger = logging.getLogger("command_center_service")
logger.info(f"Logging to: {_log_file} (maxBytes={_max_bytes}, backups={_backup_count})")

for noisy in ("httpx", "httpcore", "openai", "langchain", "langchain_core",
              "langchain_openai", "langgraph", "urllib3", "asyncio"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


# ─── Lifespan ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the graph and session manager at startup."""
    logger.info("Starting Command Center Service...")
    print_service_urls()

    # Create the LangGraph agent
    compiled_graph = None
    try:
        from graph.cc_graph import create_command_center_graph
        compiled_graph = create_command_center_graph()
        logger.info("LangGraph command center agent initialized")
    except Exception as e:
        logger.error(f"Failed to create graph: {e}")

    # Create session manager with persistent storage
    data_dir = Path(__file__).parent / "data"
    session_mgr = SessionManager(data_dir=data_dir)

    # Initialize routes
    init_health(compiled_graph, session_mgr)

    # Import and init chat routes
    try:
        from routes.chat import router as chat_router, init_chat_routes
        init_chat_routes(compiled_graph, session_mgr)
        app.include_router(chat_router)
        logger.info("Chat routes initialized")
    except Exception as e:
        logger.warning(f"Chat routes not yet available: {e}")

    # Import and init session routes
    try:
        from routes.sessions import router as sessions_router, init_session_routes
        init_session_routes(session_mgr)
        app.include_router(sessions_router)
        logger.info("Session routes initialized")
    except Exception as e:
        logger.warning(f"Session routes not yet available: {e}")

    # Import and init auth routes
    try:
        from routes.auth import router as auth_router
        app.include_router(auth_router)
        logger.info("Auth routes initialized")
    except Exception as e:
        logger.warning(f"Auth routes not yet available: {e}")

    # Import and init memory routes
    try:
        from routes.memory import router as memory_router
        app.include_router(memory_router)
        logger.info("Memory routes initialized")
    except Exception as e:
        logger.warning(f"Memory routes not yet available: {e}")

    # Import and init plugin routes
    try:
        from routes.plugins import router as plugins_router
        app.include_router(plugins_router)
        logger.info("Plugin routes initialized")
    except Exception as e:
        logger.warning(f"Plugin routes not yet available: {e}")

    # Import and init tools routes
    try:
        from routes.tools import router as tools_router
        app.include_router(tools_router)
        logger.info("Tools routes initialized")
    except Exception as e:
        logger.warning(f"Tools routes not yet available: {e}")

    # Import and init inspect routes (execution traces)
    try:
        from routes.inspect import router as inspect_router
        app.include_router(inspect_router)
        logger.info("Inspect routes initialized")
    except Exception as e:
        logger.warning(f"Inspect routes not yet available: {e}")

    # Import and init artifacts routes (with shared ArtifactManager)
    try:
        from routes.artifacts import router as artifacts_router, init_artifacts
        from command_center.artifacts.artifact_manager import ArtifactManager
        artifact_storage = str(Path(__file__).parent / "data" / "artifacts")
        artifact_mgr = ArtifactManager(artifact_storage)
        init_artifacts(artifact_mgr)
        app.include_router(artifacts_router)
        logger.info("Artifacts routes initialized")
    except Exception as e:
        logger.warning(f"Artifacts routes not yet available: {e}")

    # Import and init upload routes
    try:
        from routes.upload import router as upload_router
        app.include_router(upload_router)
        logger.info("Upload routes initialized")
    except Exception as e:
        logger.warning(f"Upload routes not yet available: {e}")

    logger.info(f"Command Center Service ready on {HOST}:{PORT}")
    yield
    logger.info("Command Center Service shutting down")


# ─── App ──────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AI Hub Command Center",
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

# Health route (always available)
app.include_router(health_router)

# Static files (frontend)
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def index():
    """Serve the main UI page."""
    index_path = os.path.join(static_dir, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return {"message": "Command Center Service running. Frontend not yet built."}


# ─── Run ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    is_frozen = getattr(sys, 'frozen', False)
    if is_frozen:
        uvicorn.run(app, host=HOST, port=PORT, reload=False)
    else:
        uvicorn.run("main:app", host=HOST, port=PORT, reload=DEBUG)
