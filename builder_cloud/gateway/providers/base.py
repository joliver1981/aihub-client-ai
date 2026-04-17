"""
Abstract base class for cloud storage providers.

Each provider (Azure Blob, AWS S3, GCS) implements this interface.
The gateway instantiates a provider per-request using the credentials
passed from the main application.
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any


class CloudStorageProvider(ABC):
    """Base class for cloud storage providers."""

    @abstractmethod
    def test_connection(self) -> Dict[str, Any]:
        """
        Test the connection to the cloud storage account.

        Returns:
            {
                'success': bool,
                'account_name': str,
                'message': str
            }
        """
        ...

    @abstractmethod
    def list_containers(self) -> Dict[str, Any]:
        """
        List all containers/buckets in the storage account.

        Returns:
            {
                'success': bool,
                'containers': [
                    {'name': str, 'last_modified': str, 'metadata': dict}
                ],
                'error': str or None
            }
        """
        ...

    @abstractmethod
    def list_objects(
        self,
        container: str,
        prefix: Optional[str] = None,
        max_results: int = 100
    ) -> Dict[str, Any]:
        """
        List objects in a container/bucket.

        Returns:
            {
                'success': bool,
                'objects': [
                    {
                        'name': str,
                        'size': int,
                        'last_modified': str,
                        'content_type': str
                    }
                ],
                'error': str or None
            }
        """
        ...

    @abstractmethod
    def upload_object(
        self,
        container: str,
        object_name: str,
        content: str,
        content_type: str = 'application/octet-stream',
        encoding: str = 'text'
    ) -> Dict[str, Any]:
        """
        Upload content to a blob/object.

        Args:
            container: Container/bucket name
            object_name: Blob/object path
            content: File content (text or base64-encoded)
            content_type: MIME type
            encoding: 'text' or 'base64'

        Returns:
            {
                'success': bool,
                'object_name': str,
                'size': int,
                'etag': str,
                'error': str or None
            }
        """
        ...

    @abstractmethod
    def download_object(
        self,
        container: str,
        object_name: str
    ) -> Dict[str, Any]:
        """
        Download a blob/object.

        Returns:
            {
                'success': bool,
                'content': str,
                'encoding': 'text' or 'base64',
                'content_type': str,
                'size': int,
                'error': str or None
            }
        """
        ...

    @abstractmethod
    def delete_object(
        self,
        container: str,
        object_name: str
    ) -> Dict[str, Any]:
        """
        Delete a blob/object.

        Returns:
            {'success': bool, 'error': str or None}
        """
        ...

    @abstractmethod
    def get_object_metadata(
        self,
        container: str,
        object_name: str
    ) -> Dict[str, Any]:
        """
        Get metadata for a blob/object.

        Returns:
            {
                'success': bool,
                'name': str,
                'size': int,
                'content_type': str,
                'last_modified': str,
                'etag': str,
                'error': str or None
            }
        """
        ...

    @abstractmethod
    def generate_sas_url(
        self,
        container: str,
        object_name: str,
        expiry_seconds: int = 3600,
        permission: str = 'read'
    ) -> Dict[str, Any]:
        """
        Generate a time-limited signed URL.

        Returns:
            {
                'success': bool,
                'url': str,
                'expires_in_seconds': int,
                'error': str or None
            }
        """
        ...
