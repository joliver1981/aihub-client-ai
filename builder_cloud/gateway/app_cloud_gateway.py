"""
Cloud Storage Gateway Service
Standalone FastAPI application for cloud storage operations.
Designed to run as a separate process with its own Python environment.

Exposes REST endpoints that the main application calls via HTTP.
All endpoints receive credentials per-request (stateless).
"""
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Add parent directories to path for CommonUtils access
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, parent_dir)
gateway_dir = os.path.dirname(__file__)
sys.path.insert(0, gateway_dir)

import logging
from logging.handlers import WatchedFileHandler
from datetime import datetime
from typing import Optional, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Try to import log rotation from CommonUtils
try:
    from CommonUtils import rotate_logs_on_startup
except ImportError:
    def rotate_logs_on_startup(path):
        pass

from cloud_gateway_config import (
    CLOUD_GATEWAY_PORT, CLOUD_GATEWAY_LOG, LOG_LEVEL,
    MAX_UPLOAD_SIZE_MB, MAX_DOWNLOAD_SIZE_MB, DEFAULT_SAS_URL_EXPIRY_SECONDS
)

# ============================================================================
# Logging Setup
# ============================================================================

log_dir = os.path.dirname(CLOUD_GATEWAY_LOG)
if log_dir:
    os.makedirs(log_dir, exist_ok=True)

rotate_logs_on_startup(CLOUD_GATEWAY_LOG)

logger = logging.getLogger("CloudGateway")
log_level = getattr(logging, LOG_LEVEL, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=CLOUD_GATEWAY_LOG, encoding='utf-8')
handler.setFormatter(formatter)
logger.addHandler(handler)

# Also log to console — reconfigure stdout for UTF-8 on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# ============================================================================
# FastAPI App
# ============================================================================

app = FastAPI(
    title="Cloud Storage Gateway",
    version="1.0.0",
    description="Cloud Storage Communication Gateway"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# Request Models
# ============================================================================

class CloudCredentials(BaseModel):
    """Credentials passed per-request from the main app."""
    provider: str = "azure_blob"
    connection_string: Optional[str] = None
    # Future: access_key_id, secret_access_key, region for S3


class TestRequest(CloudCredentials):
    pass


class ListContainersRequest(CloudCredentials):
    pass


class ListObjectsRequest(CloudCredentials):
    container: str
    prefix: Optional[str] = None
    max_results: int = Field(default=100, le=5000)


class UploadRequest(CloudCredentials):
    container: str
    object_name: str
    content: str
    content_type: str = "application/octet-stream"
    encoding: str = Field(default="text", pattern="^(text|base64)$")


class DownloadRequest(CloudCredentials):
    container: str
    object_name: str


class DeleteRequest(CloudCredentials):
    container: str
    object_name: str


class MetadataRequest(CloudCredentials):
    container: str
    object_name: str


class SasUrlRequest(CloudCredentials):
    container: str
    object_name: str
    expiry_seconds: int = Field(default=DEFAULT_SAS_URL_EXPIRY_SECONDS, le=86400)
    permission: str = Field(default="read", pattern="^(read|write)$")


# ============================================================================
# Provider Factory
# ============================================================================

def get_provider(creds: CloudCredentials):
    """Create a provider instance from credentials."""
    if creds.provider == "azure_blob":
        if not creds.connection_string:
            raise HTTPException(status_code=400, detail="connection_string is required for Azure Blob")
        from providers.azure_blob import AzureBlobProvider
        return AzureBlobProvider(connection_string=creds.connection_string)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {creds.provider}")


# ============================================================================
# Endpoints
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "ok",
        "message": "Cloud Storage Gateway is operational",
        "service": "cloud-storage-gateway",
        "timestamp": datetime.utcnow().isoformat(),
        "supported_providers": ["azure_blob"]
    }


@app.post("/api/cloud/test")
async def test_connection(req: TestRequest):
    """Test a cloud storage connection."""
    logger.info(f"Testing connection for provider: {req.provider}")
    provider = get_provider(req)
    result = provider.test_connection()
    logger.info(f"Connection test result: {'success' if result.get('success') else 'failed'}")
    return result


@app.post("/api/cloud/containers")
async def list_containers(req: ListContainersRequest):
    """List containers/buckets."""
    logger.debug(f"Listing containers for provider: {req.provider}")
    provider = get_provider(req)
    result = provider.list_containers()
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error', 'Unknown error'))
    return result


@app.post("/api/cloud/objects")
async def list_objects(req: ListObjectsRequest):
    """List objects in a container."""
    logger.debug(f"Listing objects in {req.container} (prefix={req.prefix}, max={req.max_results})")
    provider = get_provider(req)
    result = provider.list_objects(req.container, req.prefix, req.max_results)
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error', 'Unknown error'))
    return result


@app.post("/api/cloud/upload")
async def upload_object(req: UploadRequest):
    """Upload content to a blob/object."""
    # Check content size
    content_size_mb = len(req.content) / (1024 * 1024)
    if content_size_mb > MAX_UPLOAD_SIZE_MB:
        raise HTTPException(
            status_code=413,
            detail=f"Content exceeds maximum upload size of {MAX_UPLOAD_SIZE_MB}MB"
        )

    logger.info(f"Uploading {req.object_name} to {req.container} "
                f"(type={req.content_type}, encoding={req.encoding}, size={len(req.content)} chars)")
    provider = get_provider(req)
    result = provider.upload_object(
        req.container, req.object_name, req.content,
        req.content_type, req.encoding
    )
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error', 'Unknown error'))
    return result


@app.post("/api/cloud/download")
async def download_object(req: DownloadRequest):
    """Download a blob/object."""
    logger.info(f"Downloading {req.object_name} from {req.container}")
    provider = get_provider(req)

    # Check size before downloading
    metadata = provider.get_object_metadata(req.container, req.object_name)
    if metadata.get('success') and metadata.get('size', 0) > MAX_DOWNLOAD_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum download size of {MAX_DOWNLOAD_SIZE_MB}MB. "
                   f"Use generate_sas_url to get a direct download link instead."
        )

    result = provider.download_object(req.container, req.object_name)
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error', 'Unknown error'))
    return result


@app.post("/api/cloud/delete")
async def delete_object(req: DeleteRequest):
    """Delete a blob/object."""
    logger.info(f"Deleting {req.object_name} from {req.container}")
    provider = get_provider(req)
    result = provider.delete_object(req.container, req.object_name)
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error', 'Unknown error'))
    return result


@app.post("/api/cloud/metadata")
async def get_object_metadata(req: MetadataRequest):
    """Get metadata for a blob/object."""
    logger.debug(f"Getting metadata for {req.object_name} in {req.container}")
    provider = get_provider(req)
    result = provider.get_object_metadata(req.container, req.object_name)
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error', 'Unknown error'))
    return result


@app.post("/api/cloud/sas-url")
async def generate_sas_url(req: SasUrlRequest):
    """Generate a time-limited SAS/presigned URL."""
    logger.info(f"Generating SAS URL for {req.object_name} in {req.container} "
                f"(expiry={req.expiry_seconds}s, permission={req.permission})")
    provider = get_provider(req)
    result = provider.generate_sas_url(
        req.container, req.object_name,
        req.expiry_seconds, req.permission
    )
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error', 'Unknown error'))
    return result


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == '__main__':
    import uvicorn

    port = CLOUD_GATEWAY_PORT
    logger.info(f"Starting Cloud Storage Gateway on port {port}")

    is_frozen = getattr(sys, 'frozen', False)

    if is_frozen:
        uvicorn.run(app, host="0.0.0.0", port=port, reload=False)
    else:
        uvicorn.run("app_cloud_gateway:app", host="0.0.0.0", port=port, reload=False)
