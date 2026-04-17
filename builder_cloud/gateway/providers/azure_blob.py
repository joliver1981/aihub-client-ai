"""
Azure Blob Storage Provider

Uses the azure-storage-blob SDK to interact with Azure Blob Storage.
Instantiated per-request with credentials passed from the main app.
"""
import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional

from azure.storage.blob import (
    BlobServiceClient,
    BlobSasPermissions,
    generate_blob_sas,
)

from providers.base import CloudStorageProvider

logger = logging.getLogger("CloudGateway.AzureBlob")

# Content types that are safe to return as text
TEXT_CONTENT_TYPES = {
    'text/plain', 'text/csv', 'text/html', 'text/xml', 'text/tab-separated-values',
    'application/json', 'application/xml', 'application/javascript',
    'application/x-yaml', 'application/yaml',
}

# File extensions that are safe to return as text
TEXT_EXTENSIONS = {
    '.txt', '.csv', '.json', '.xml', '.html', '.htm', '.yaml', '.yml',
    '.md', '.log', '.tsv', '.sql', '.py', '.js', '.css',
}


def _is_text_content(content_type: str, blob_name: str) -> bool:
    """Determine if content should be returned as text based on content type or extension."""
    if content_type:
        base_type = content_type.split(';')[0].strip().lower()
        if base_type in TEXT_CONTENT_TYPES:
            return True
        if base_type.startswith('text/'):
            return True
    ext = '.' + blob_name.rsplit('.', 1)[-1].lower() if '.' in blob_name else ''
    return ext in TEXT_EXTENSIONS


class AzureBlobProvider(CloudStorageProvider):
    """Azure Blob Storage provider using connection string auth."""

    def __init__(self, connection_string: str):
        """
        Args:
            connection_string: Azure Storage connection string
        """
        self.connection_string = connection_string
        self._client = None

    @property
    def client(self) -> BlobServiceClient:
        if self._client is None:
            self._client = BlobServiceClient.from_connection_string(self.connection_string)
        return self._client

    def test_connection(self) -> Dict[str, Any]:
        try:
            props = self.client.get_account_information()
            return {
                'success': True,
                'account_name': self.client.account_name,
                'message': f"Connected to Azure Blob Storage account '{self.client.account_name}' "
                           f"(SKU: {props.get('sku_name', 'unknown')}, Kind: {props.get('account_kind', 'unknown')})"
            }
        except Exception as e:
            return {
                'success': False,
                'account_name': None,
                'message': f"Connection failed: {str(e)}"
            }

    def list_containers(self) -> Dict[str, Any]:
        try:
            containers = []
            for c in self.client.list_containers():
                containers.append({
                    'name': c['name'],
                    'last_modified': c['last_modified'].isoformat() if c.get('last_modified') else None,
                    'metadata': dict(c.get('metadata', {})) if c.get('metadata') else {}
                })
            return {
                'success': True,
                'containers': containers,
                'error': None
            }
        except Exception as e:
            logger.error(f"Error listing containers: {e}")
            return {
                'success': False,
                'containers': [],
                'error': str(e)
            }

    def list_objects(
        self,
        container: str,
        prefix: Optional[str] = None,
        max_results: int = 100
    ) -> Dict[str, Any]:
        try:
            container_client = self.client.get_container_client(container)
            blobs = []
            count = 0
            for blob in container_client.list_blobs(name_starts_with=prefix):
                if count >= max_results:
                    break
                blobs.append({
                    'name': blob.name,
                    'size': blob.size,
                    'last_modified': blob.last_modified.isoformat() if blob.last_modified else None,
                    'content_type': blob.content_settings.content_type if blob.content_settings else None
                })
                count += 1
            return {
                'success': True,
                'objects': blobs,
                'error': None
            }
        except Exception as e:
            logger.error(f"Error listing objects in {container}: {e}")
            return {
                'success': False,
                'objects': [],
                'error': str(e)
            }

    def upload_object(
        self,
        container: str,
        object_name: str,
        content: str,
        content_type: str = 'application/octet-stream',
        encoding: str = 'text'
    ) -> Dict[str, Any]:
        try:
            if encoding == 'base64':
                data = base64.b64decode(content)
            else:
                data = content.encode('utf-8')

            blob_client = self.client.get_blob_client(container, object_name)
            result = blob_client.upload_blob(
                data,
                overwrite=True,
                content_settings={'content_type': content_type}
            )

            return {
                'success': True,
                'object_name': object_name,
                'size': len(data),
                'etag': result.get('etag', '').strip('"') if result.get('etag') else None,
                'error': None
            }
        except Exception as e:
            logger.error(f"Error uploading {object_name} to {container}: {e}")
            return {
                'success': False,
                'object_name': object_name,
                'size': 0,
                'etag': None,
                'error': str(e)
            }

    def download_object(
        self,
        container: str,
        object_name: str
    ) -> Dict[str, Any]:
        try:
            blob_client = self.client.get_blob_client(container, object_name)
            download = blob_client.download_blob()
            props = download.properties

            content_type = props.content_settings.content_type if props.content_settings else 'application/octet-stream'
            raw_data = download.readall()
            size = len(raw_data)

            if _is_text_content(content_type, object_name):
                try:
                    content = raw_data.decode('utf-8')
                    result_encoding = 'text'
                except UnicodeDecodeError:
                    content = base64.b64encode(raw_data).decode('ascii')
                    result_encoding = 'base64'
            else:
                content = base64.b64encode(raw_data).decode('ascii')
                result_encoding = 'base64'

            return {
                'success': True,
                'content': content,
                'encoding': result_encoding,
                'content_type': content_type,
                'size': size,
                'error': None
            }
        except Exception as e:
            logger.error(f"Error downloading {object_name} from {container}: {e}")
            return {
                'success': False,
                'content': None,
                'encoding': None,
                'content_type': None,
                'size': 0,
                'error': str(e)
            }

    def delete_object(
        self,
        container: str,
        object_name: str
    ) -> Dict[str, Any]:
        try:
            blob_client = self.client.get_blob_client(container, object_name)
            blob_client.delete_blob()
            return {'success': True, 'error': None}
        except Exception as e:
            logger.error(f"Error deleting {object_name} from {container}: {e}")
            return {'success': False, 'error': str(e)}

    def get_object_metadata(
        self,
        container: str,
        object_name: str
    ) -> Dict[str, Any]:
        try:
            blob_client = self.client.get_blob_client(container, object_name)
            props = blob_client.get_blob_properties()
            return {
                'success': True,
                'name': object_name,
                'size': props.size,
                'content_type': props.content_settings.content_type if props.content_settings else None,
                'last_modified': props.last_modified.isoformat() if props.last_modified else None,
                'etag': props.etag.strip('"') if props.etag else None,
                'error': None
            }
        except Exception as e:
            logger.error(f"Error getting metadata for {object_name} in {container}: {e}")
            return {
                'success': False,
                'name': object_name,
                'size': 0,
                'content_type': None,
                'last_modified': None,
                'etag': None,
                'error': str(e)
            }

    def generate_sas_url(
        self,
        container: str,
        object_name: str,
        expiry_seconds: int = 3600,
        permission: str = 'read'
    ) -> Dict[str, Any]:
        try:
            if permission == 'write':
                sas_permission = BlobSasPermissions(read=True, write=True, create=True)
            else:
                sas_permission = BlobSasPermissions(read=True)

            # Parse account name and key from connection string
            parts = dict(pair.split('=', 1) for pair in self.connection_string.split(';') if '=' in pair)
            account_name = parts.get('AccountName', '')
            account_key = parts.get('AccountKey', '')

            if not account_name or not account_key:
                return {
                    'success': False,
                    'url': None,
                    'expires_in_seconds': 0,
                    'error': 'Cannot generate SAS URL: connection string must contain AccountName and AccountKey'
                }

            sas_token = generate_blob_sas(
                account_name=account_name,
                container_name=container,
                blob_name=object_name,
                account_key=account_key,
                permission=sas_permission,
                expiry=datetime.now(timezone.utc) + timedelta(seconds=expiry_seconds)
            )

            url = f"https://{account_name}.blob.core.windows.net/{container}/{object_name}?{sas_token}"

            return {
                'success': True,
                'url': url,
                'expires_in_seconds': expiry_seconds,
                'error': None
            }
        except Exception as e:
            logger.error(f"Error generating SAS URL for {object_name} in {container}: {e}")
            return {
                'success': False,
                'url': None,
                'expires_in_seconds': 0,
                'error': str(e)
            }
