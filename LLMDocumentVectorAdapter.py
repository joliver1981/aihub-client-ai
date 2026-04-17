import os
import logging
from typing import List, Dict, Any, Optional, Union

from LLMDocumentVectorEngine import LLMDocumentVectorEngine, ChromaDBStore
from vector_engine_client import VectorEngineClient
from CommonUtils import get_vector_api_base_url

class LLMDocumentVectorAdapter:
    """
    Adapter class to make it easy to migrate from direct ChromaDB usage to
    either the local LLMDocumentVectorEngine or the remote API client.
    
    This adapter maintains the same interface used in your existing code, but
    internally uses the new vector engine abstractions.
    """
    
    def __init__(
        self,
        use_remote: bool = True,
        remote_url: str = get_vector_api_base_url(),
        api_key: Optional[str] = None,
        vector_db_path: str = "./chroma_db",
        collection_name: str = "documents",
        log_level: str = "INFO"
    ):
        """
        Initialize the document vector adapter
        
        Args:
            use_remote: Whether to use the remote API or local engine
            remote_url: URL of the remote vector engine API (if use_remote=True)
            api_key: API key for the remote API (if use_remote=True)
            vector_db_path: Path to vector database storage (if use_remote=False)
            collection_name: Collection name to use (if use_remote=False)
            log_level: Logging level
        """
        # Set up logging
        self.logger = logging.getLogger("DocumentVectorAdapter")
        self.logger.setLevel(getattr(logging, log_level))
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
        
        self.use_remote = use_remote
        
        if use_remote:
            # Initialize remote client
            self.logger.info(f"Using remote vector engine at {remote_url}")
            self.client = VectorEngineClient(
                base_url=remote_url,
                api_key=api_key
            )
            self.engine = None
        else:
            # Initialize local engine
            self.logger.info(f"Using local vector engine with {vector_db_path}")
            self.engine = LLMDocumentVectorEngine()
            self.engine.initialize(
                path=vector_db_path,
                collection_name=collection_name
            )
            self.client = None
    
    def add(self, documents: List[str], metadatas: List[Dict[str, Any]], 
           ids: List[str]) -> None:
        """
        Add documents to the vector store (compatible with ChromaDB interface)
        
        Args:
            documents: List of document texts
            metadatas: List of metadata dictionaries
            ids: List of unique IDs for the documents
        """
        try:
            print(f"Adding {len(documents)} documents to vector DB")
            print(f"Documents: {documents}")
            print(f"Metadatas: {metadatas}")
            print(f"Ids: {ids}")
            if self.use_remote:
                # Convert parameters to the format expected by the API
                docs_for_api = [
                    {"id": id, "text": doc, "metadata": meta}
                    for id, doc, meta in zip(ids, documents, metadatas)
                ]
                
                # Call the API
                print(f"Calling API to add documents...")
                print(f"Docs for API: {docs_for_api}")
                response = self.client.add_documents_batch(docs_for_api)
                
                # Check response
                if response.get("status") != "success":
                    self.logger.error(f"Error adding documents: {response.get('message')}")
                    raise RuntimeError(f"Error adding documents: {response.get('message')}")
                    
            else:
                # Use local engine
                print(f"Using local engine to add documents...")
                for i in range(len(documents)):
                    self.engine.add_document(
                        document_text=documents[i],
                        metadata=metadatas[i],
                        doc_id=ids[i]
                    )
                    
        except Exception as e:
            self.logger.error(f"Error adding documents: {str(e)}")
            raise
    
    def query(self, query_texts: List[str], where: Optional[Dict[str, Any]] = None,
             n_results: int = 10, **kwargs) -> Dict[str, Any]:
        """
        Search for documents (compatible with ChromaDB interface)
        
        Args:
            query_texts: List of query strings (only first is used)
            where: Optional filter conditions
            n_results: Maximum number of results to return
            **kwargs: Additional parameters
            
        Returns:
            Dictionary with search results in ChromaDB format
        """
        try:
            if not query_texts:
                raise ValueError("No query texts provided")
                
            # Get parameters
            query = query_texts[0]  # Use the first query
            min_score = float(kwargs.get('min_score', 0.0))
            
            if self.use_remote:
                # Call the API
                response = self.client.search(
                    query=query,
                    filters=where,
                    limit=n_results,
                    min_score=min_score
                )
                
                # Check response
                if response.get("status") != "success":
                    self.logger.error(f"Error searching documents: {response.get('message')}")
                    raise RuntimeError(f"Error searching documents: {response.get('message')}")
                
                # Convert results to the format expected by the original code
                results = response.get("results", [])
                
                # Prepare the result in ChromaDB format
                # This is a simplification - may need adjustments based on your exact usage
                return {
                    "ids": [[r["document_id"] for r in results]],
                    "documents": [[r["text"] for r in results]],
                    "metadatas": [[r["metadata"] for r in results]],
                    "distances": [[1 - r["relevance_score"] for r in results]]
                }
                
            else:
                # Use local engine
                results = self.engine.search(
                    query=query,
                    filters=where,
                    limit=n_results,
                    min_score=min_score
                )
                
                # Convert results to the format expected by the original code
                return {
                    "ids": [[r["document_id"] for r in results]],
                    "documents": [[r["text"] for r in results]],
                    "metadatas": [[r["metadata"] for r in results]],
                    "distances": [[1 - r["relevance_score"] for r in results]]
                }
                
        except Exception as e:
            self.logger.error(f"Error searching documents: {str(e)}")
            # Return empty results on error
            return {
                "ids": [[]],
                "documents": [[]],
                "metadatas": [[]],
                "distances": [[]]
            }
    
    def get(self, ids: Optional[List[str]] = None, 
           where: Optional[Dict[str, Any]] = None,
           limit: Optional[int] = None,
           include: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Get documents by ID or filter conditions (compatible with ChromaDB interface)
        
        Args:
            ids: Optional list of document IDs to retrieve
            where: Optional filter conditions
            limit: Optional maximum number of results
            include: Optional list of data to include
            
        Returns:
            Dictionary with documents in ChromaDB format
        """
        try:
            # Handle different parameter combinations
            if ids and len(ids) == 1:
                # Get a single document by ID
                if self.use_remote:
                    response = self.client.get_document(ids[0])
                    
                    # Check response
                    if response.get("status") != "success":
                        self.logger.error(f"Error getting document: {response.get('message')}")
                        return {"ids": [], "documents": [], "metadatas": []}
                        
                    # Extract the document data
                    doc_data = response.get("data", {})
                    
                    return {
                        "ids": [doc_data.get("id", ids[0])],
                        "documents": [doc_data.get("document", "")],
                        "metadatas": [doc_data.get("metadata", {})]
                    }
                    
                else:
                    # Use local engine
                    doc_data = self.engine.get_document(ids[0])
                    
                    if doc_data:
                        return {
                            "ids": [doc_data.get("id", ids[0])],
                            "documents": [doc_data.get("document", "")],
                            "metadatas": [doc_data.get("metadata", {})]
                        }
                    else:
                        return {"ids": [], "documents": [], "metadatas": []}
            
            else:
                # This is a more complex get operation, not fully implemented in this adapter
                # You may need to extend this based on your specific usage patterns
                self.logger.warning("Complex get operations are not fully implemented in the adapter")
                return {"ids": [], "documents": [], "metadatas": []}
                
        except Exception as e:
            self.logger.error(f"Error getting documents: {str(e)}")
            return {"ids": [], "documents": [], "metadatas": []}
    
    def delete(self, ids: Optional[List[str]] = None, 
          where: Optional[Dict[str, Any]] = None) -> Optional[bool]:
        """
        Delete documents by ID or filter conditions (compatible with ChromaDB interface)
        Handles both regular document IDs and page_ids that may have chunks
        
        Args:
            ids: Optional list of document IDs to delete (can be page_ids that need to find chunks)
            where: Optional filter conditions
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if ids:
                for doc_id in ids:
                    try:
                        if self.use_remote:
                            # First try to delete the document directly (for non-chunked docs)
                            response = self.client.delete_document(doc_id)
                            
                            # Also try to delete potential chunks for this page_id
                            # Check for chunks with pattern: doc_id_chunk_0, doc_id_chunk_1, etc.
                            chunk_count = 0
                            while chunk_count < 100:  # Reasonable limit
                                chunk_id = f"{doc_id}_chunk_{chunk_count}"
                                chunk_response = self.client.delete_document(chunk_id)
                                if chunk_response.get("status") != "success":
                                    break  # No more chunks found
                                chunk_count += 1
                            
                            if chunk_count > 0:
                                self.logger.info(f"Deleted {chunk_count} chunks for page {doc_id}")
                            elif response.get("status") == "success":
                                self.logger.info(f"Deleted document {doc_id}")
                            else:
                                self.logger.warning(f"Could not delete document or chunks for {doc_id}")
                                
                        else:
                            # Use local engine - it should handle chunk deletion automatically
                            success = self.engine.delete_document(doc_id)
                            if not success:
                                self.logger.warning(f"Could not delete document {doc_id}")
                                
                    except Exception as e:
                        self.logger.error(f"Error deleting document {doc_id}: {str(e)}")
                        continue
                        
                return True
                
            else:
                # Deletion by filter not implemented in this adapter
                self.logger.warning("Deletion by filter is not implemented in the adapter")
                return False
                
        except Exception as e:
            self.logger.error(f"Error deleting documents: {str(e)}")
            return False
    
    def close(self) -> None:
        """Close connections"""
        if not self.use_remote and self.engine:
            self.engine.close()


    def get_collection_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the collection (compatible with ChromaDB interface)
        
        Returns:
            Dictionary with collection statistics
        """
        try:
            if self.use_remote:
                # Call the API
                response = self.client.get_stats()
                
                # Check response
                if response.get("status") != "success":
                    self.logger.error(f"Error getting collection stats: {response.get('message')}")
                    return {"count": 0, "metadata_fields": []}
                    
                # Return the data
                return response.get("data", {"count": 0, "metadata_fields": []})
                
            else:
                # Use local engine
                return self.engine.get_stats()
                
        except Exception as e:
            self.logger.error(f"Error getting collection stats: {str(e)}")
            return {"count": 0, "metadata_fields": []}
        