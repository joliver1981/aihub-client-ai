"""
Cloud Storage Routes
Flask Blueprint for cloud-storage-specific API endpoints.

Most cloud storage operations route through the standard integration
endpoints (/api/integrations/<id>/execute). This blueprint adds
supplementary endpoints like gateway health checks.
"""
import logging
from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)

cloud_bp = Blueprint('cloud_storage', __name__, url_prefix='/api/cloud-storage')


@cloud_bp.route('/health', methods=['GET'])
def cloud_gateway_health():
    """Proxy health check to the Cloud Storage Gateway service."""
    try:
        from builder_cloud.client.cloud_storage_client import CloudStorageClient
        client = CloudStorageClient()
        healthy = client.health_check()
        if healthy:
            return jsonify({
                'status': 'ok',
                'message': 'Cloud Storage Gateway is operational'
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Cloud Storage Gateway is not responding'
            }), 503
    except Exception as e:
        logger.error(f"Cloud Storage Gateway health check failed: {e}")
        return jsonify({
            'status': 'error',
            'message': f'Cloud Storage Gateway health check failed: {str(e)}'
        }), 503
