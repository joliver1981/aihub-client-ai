"""
Cloud Storage Gateway Client
Client module for communicating with the Cloud Storage Gateway service.
Used by the main application to interact with cloud storage providers.

Dependencies: requests only (no cloud SDKs needed)
"""
import os
import requests
import logging
from typing import Dict, Optional
from urllib.parse import urljoin
from CommonUtils import get_cloud_storage_api_base_url

logger = logging.getLogger(__name__)


class CloudStorageClient:
    """Client for the Cloud Storage Gateway microservice."""

    def __init__(self, base_url: str = None, timeout: int = 60, max_retries: int = 3):
        """
        Initialize the Cloud Storage Gateway client.

        Args:
            base_url: Base URL of the Cloud Storage Gateway service
            timeout: Default request timeout in seconds
            max_retries: Maximum number of retry attempts
        """
        self.base_url = base_url or os.getenv('CLOUD_GATEWAY_URL', get_cloud_storage_api_base_url())
        self.timeout = timeout
        self.max_retries = max_retries

        # Set up retry logic
        from requests.adapters import HTTPAdapter
        from requests.packages.urllib3.util.retry import Retry

        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        self._adapter = HTTPAdapter(max_retries=retry_strategy)

    def _make_request(self, method: str, endpoint: str, **kwargs) -> Dict:
        """
        Make a request to the Cloud Storage Gateway.

        Returns:
            Response data as dictionary

        Raises:
            Exception: If request fails after retries
        """
        url = urljoin(self.base_url + '/', endpoint.lstrip('/'))

        if 'timeout' not in kwargs:
            kwargs['timeout'] = self.timeout

        if 'headers' not in kwargs:
            kwargs['headers'] = {}
        kwargs['headers']['Connection'] = 'close'

        try:
            response = requests.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            logger.error(f"Cloud Storage Gateway timeout: {url}")
            raise Exception(f"Cloud Storage Gateway request timed out after {kwargs.get('timeout', self.timeout)}s")
        except requests.exceptions.ConnectionError:
            logger.error(f"Cloud Storage Gateway connection error: {url}")
            raise Exception(f"Could not connect to Cloud Storage Gateway at {self.base_url}")
        except requests.exceptions.HTTPError as e:
            # Try to extract detail from FastAPI error response
            try:
                detail = e.response.json().get('detail', str(e))
            except Exception:
                detail = str(e)
            logger.error(f"Cloud Storage Gateway HTTP error: {detail}")
            raise Exception(f"Cloud Storage Gateway error: {detail}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Cloud Storage Gateway request failed: {e}")
            raise Exception(f"Cloud Storage Gateway request failed: {str(e)}")

    def health_check(self) -> bool:
        """Check if the Cloud Storage Gateway service is accessible."""
        try:
            r = self._make_request('GET', '/health')
            return r.get('status') == 'ok'
        except Exception:
            return False

    def test_connection(self, provider: str, credentials: Dict) -> Dict:
        """Test a cloud storage connection."""
        payload = {'provider': provider}
        payload.update(credentials)
        return self._make_request('POST', '/api/cloud/test', json=payload)

    def list_containers(self, provider: str, credentials: Dict) -> Dict:
        """List containers/buckets."""
        payload = {'provider': provider}
        payload.update(credentials)
        return self._make_request('POST', '/api/cloud/containers', json=payload)

    def list_objects(
        self,
        provider: str,
        credentials: Dict,
        container: str,
        prefix: Optional[str] = None,
        max_results: int = 100
    ) -> Dict:
        """List objects in a container."""
        payload = {
            'provider': provider,
            'container': container,
            'prefix': prefix,
            'max_results': max_results
        }
        payload.update(credentials)
        return self._make_request('POST', '/api/cloud/objects', json=payload)

    def upload_object(
        self,
        provider: str,
        credentials: Dict,
        container: str,
        object_name: str,
        content: str,
        content_type: str = 'application/octet-stream',
        encoding: str = 'text'
    ) -> Dict:
        """Upload content to a blob/object."""
        payload = {
            'provider': provider,
            'container': container,
            'object_name': object_name,
            'content': content,
            'content_type': content_type,
            'encoding': encoding
        }
        payload.update(credentials)
        return self._make_request('POST', '/api/cloud/upload', json=payload)

    def download_object(
        self,
        provider: str,
        credentials: Dict,
        container: str,
        object_name: str
    ) -> Dict:
        """Download a blob/object."""
        payload = {
            'provider': provider,
            'container': container,
            'object_name': object_name
        }
        payload.update(credentials)
        return self._make_request('POST', '/api/cloud/download', json=payload)

    def delete_object(
        self,
        provider: str,
        credentials: Dict,
        container: str,
        object_name: str
    ) -> Dict:
        """Delete a blob/object."""
        payload = {
            'provider': provider,
            'container': container,
            'object_name': object_name
        }
        payload.update(credentials)
        return self._make_request('POST', '/api/cloud/delete', json=payload)

    def get_object_metadata(
        self,
        provider: str,
        credentials: Dict,
        container: str,
        object_name: str
    ) -> Dict:
        """Get metadata for a blob/object."""
        payload = {
            'provider': provider,
            'container': container,
            'object_name': object_name
        }
        payload.update(credentials)
        return self._make_request('POST', '/api/cloud/metadata', json=payload)

    def generate_sas_url(
        self,
        provider: str,
        credentials: Dict,
        container: str,
        object_name: str,
        expiry_seconds: int = 3600,
        permission: str = 'read'
    ) -> Dict:
        """Generate a time-limited SAS/presigned URL."""
        payload = {
            'provider': provider,
            'container': container,
            'object_name': object_name,
            'expiry_seconds': expiry_seconds,
            'permission': permission
        }
        payload.update(credentials)
        return self._make_request('POST', '/api/cloud/sas-url', json=payload)
