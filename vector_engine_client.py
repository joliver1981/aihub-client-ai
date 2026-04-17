import requests
import json
import os
from typing import Dict, Any, List, Optional
from CommonUtils import get_vector_api_base_url


class VectorEngineClient:
    """
    Client for the Document Vector Engine API.
    Provides a simple interface for interacting with the vector engine microservice.
    """
    
    def __init__(self, base_url: str = get_vector_api_base_url(), api_key: Optional[str] = None):
        """
        Initialize the vector engine client.
        
        Args:
            base_url: Base URL of the vector engine API
            api_key: API key for authentication (optional)
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key or os.getenv('VECTOR_API_KEY', '')
        
        # Setup default headers
        self.headers = {
            'Content-Type': 'application/json'
        }
        
        if self.api_key:
            self.headers['X-API-Key'] = self.api_key
    
    def health_check(self) -> Dict[str, Any]:
        """Check if the API is operational"""
        response = requests.get(f"{self.base_url}/health")
        return response.json()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get vector database statistics"""
        response = requests.get(
            f"{self.base_url}/stats",
            headers=self.headers
        )
        return response.json()
    
    def add_document(self, doc_id: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Add a single document to the vector store
        
        Args:
            doc_id: Unique document ID
            text: Document text content
            metadata: Document metadata
            
        Returns:
            API response
        """
        payload = {
            'id': doc_id,
            'text': text,
            'metadata': metadata
        }
        
        response = requests.post(
            f"{self.base_url}/documents",
            headers=self.headers,
            json=payload
        )
        
        return response.json()
    
    def add_documents_batch(self, documents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Add multiple documents to the vector store in a batch
        
        Args:
            documents: List of document dictionaries with 'id', 'text', and 'metadata' keys
            
        Returns:
            API response
        """
        response = requests.post(
            f"{self.base_url}/documents/batch",
            headers=self.headers,
            json=documents
        )
        
        return response.json()
    
    def get_document(self, doc_id: str) -> Dict[str, Any]:
        """
        Get a document by ID
        
        Args:
            doc_id: Document ID to retrieve
            
        Returns:
            API response
        """
        response = requests.get(
            f"{self.base_url}/documents/{doc_id}",
            headers=self.headers
        )
        
        return response.json()
    
    def update_document(self, doc_id: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing document
        
        Args:
            doc_id: Document ID to update
            text: New document text
            metadata: New metadata
            
        Returns:
            API response
        """
        payload = {
            'text': text,
            'metadata': metadata
        }
        
        response = requests.put(
            f"{self.base_url}/documents/{doc_id}",
            headers=self.headers,
            json=payload
        )
        
        return response.json()
    
    def delete_document(self, doc_id: str) -> Dict[str, Any]:
        """
        Delete a document by ID
        
        Args:
            doc_id: Document ID to delete
            
        Returns:
            API response
        """
        response = requests.delete(
            f"{self.base_url}/documents/{doc_id}",
            headers=self.headers
        )
        
        return response.json()
    
    def search(self, query: str, filters: Optional[Dict[str, Any]] = None, 
              limit: int = 10, min_score: float = 0.0) -> Dict[str, Any]:
        """
        Search for documents similar to the query
        
        Args:
            query: Text query to search for
            filters: Optional metadata filters
            limit: Maximum number of results to return
            min_score: Minimum relevance score (0-1) to include in results
            
        Returns:
            API response with search results
        """
        payload = {
            'query': query,
            'limit': limit,
            'min_score': min_score
        }
        
        if filters:
            payload['filters'] = filters
        
        response = requests.post(
            f"{self.base_url}/search",
            headers=self.headers,
            json=payload
        )
        
        return response.json()
    

    def search_for_ai(self, query: str, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Search for documents similar to the query with output formatted for AI ingestion
        
        Args:
            query: Text query to search for
            filters: Optional metadata filters
            
        Returns:
            API response with search results
        """
        payload = {
            'query': query
        }
        
        if filters:
            payload['filters'] = filters
        
        response = requests.post(
            f"{self.base_url}/search_for_ai",
            headers=self.headers,
            json=payload
        )
        
        return response.json()


# Example usage
if __name__ == "__main__":
    # Create client instance
    client = VectorEngineClient(
        base_url="http://localhost:5002",
        api_key="your_api_key"  # Replace with your actual API key or set VECTOR_API_KEY env var
    )
    
    # Check API health
    print("Health check:")
    print(json.dumps(client.health_check(), indent=2))
    
    # Add a document
    print("\nAdding document:")
    add_response = client.add_document(
        doc_id="example-doc-1",
        text="This is an example document about artificial intelligence and machine learning.",
        metadata={
            "title": "AI Basics",
            "author": "John Doe",
            "date": "2025-05-16",
            "category": "Technology"
        }
    )
    print(json.dumps(add_response, indent=2))
    
    # Add a batch of documents
    print("\nAdding document batch:")
    batch_documents = [
        {
            "id": "example-doc-2",
            "text": "Vector databases are essential for efficient similarity search in AI applications.",
            "metadata": {
                "title": "Vector DBs",
                "category": "Database"
            }
        },
        {
            "id": "example-doc-3",
            "text": "Microservices architecture allows for better scalability and separation of concerns.",
            "metadata": {
                "title": "Microservices",
                "category": "Architecture"
            }
        }
    ]
    batch_response = client.add_documents_batch(batch_documents)
    print(json.dumps(batch_response, indent=2))
    
    # Search for documents
    print("\nSearching documents:")
    search_response = client.search(
        query="vector database similarity search",
        filters={"category": "Database"},
        limit=5,
        min_score=0.5
    )
    print(json.dumps(search_response, indent=2))
    
    # Get document
    print("\nGetting document:")
    get_response = client.get_document("example-doc-1")
    print(json.dumps(get_response, indent=2))
    
    # Get statistics
    print("\nGetting stats:")
    stats_response = client.get_stats()
    print(json.dumps(stats_response, indent=2))
