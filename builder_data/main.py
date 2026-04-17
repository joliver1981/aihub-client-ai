"""
Builder Data Service — Main Application
==========================================
FastAPI app that serves the data pipeline agent
and provides SSE chat + REST API endpoints.

Run with:
    uvicorn main:app --host 0.0.0.0 --port 8200 --reload
"""

import logging
import os
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from builder_data_config import HOST, PORT, DEBUG, CORS_ORIGINS, AI_HUB_BASE_URL, AI_HUB_API_KEY, print_service_urls
from execution.connection_bridge import ConnectionBridge
from execution.pipeline_executor import PipelineExecutor
from services import SessionManager
from routes.chat import router as chat_router, init_chat_routes
from routes.pipelines import router as pipeline_router, init_pipeline_routes
from routes.quality import router as quality_router, init_quality_routes
from routes.connections import router as connection_router, init_connection_routes

# ─── Logging ──────────────────────────────────────────────────────────────

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-24s | %(levelname)-7s | %(message)s",
)
logger = logging.getLogger("builder_data")

# Quiet down noisy third-party loggers
for noisy in ("httpx", "httpcore", "openai", "langchain", "langchain_core",
              "langchain_openai", "langgraph", "urllib3", "asyncio"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


# ─── Lifespan ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize services at startup."""
    logger.info("Starting Builder Data Service...")

    print_service_urls()

    # Initialize connection bridge to main app
    bridge = ConnectionBridge(
        main_app_url=AI_HUB_BASE_URL,
        api_key=AI_HUB_API_KEY,
    )
    logger.info(f"Connection bridge initialized (main app: {AI_HUB_BASE_URL})")

    # Initialize pipeline executor
    executor = PipelineExecutor(bridge)
    logger.info("Pipeline executor initialized")

    # Create the LangGraph data agent
    compiled_graph = None
    try:
        from graph.data_graph import create_data_graph
        compiled_graph = create_data_graph(bridge)
        logger.info("LangGraph data agent initialized")
    except Exception as e:
        logger.error(f"Failed to create data graph: {e}", exc_info=True)

    # Create session manager
    session_mgr = SessionManager()

    # Wire into routes
    init_chat_routes(compiled_graph, session_mgr)
    init_pipeline_routes(executor)
    init_quality_routes(bridge)
    init_connection_routes(bridge)

    logger.info(f"Builder Data Service ready on {HOST}:{PORT}")
    yield

    # Cleanup
    await bridge.close()
    logger.info("Builder Data Service shutting down")


# ─── App ──────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AI Hub Data Pipeline Agent",
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
app.include_router(pipeline_router)
app.include_router(quality_router)
app.include_router(connection_router)


@app.get("/")
async def index():
    """Root endpoint."""
    return {
        "service": "AI Hub Data Pipeline Agent",
        "version": "0.1.0",
        "status": "running",
    }


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
