import os
import json
import logging
from typing import List, Dict, Any, Optional, Union, Type
from abc import ABC, abstractmethod

import chromadb
from chromadb.config import Settings
import config as cfg
import hashlib
from TextChunker_LLM import TextChunker


class VectorStore(ABC):
    """
    Abstract base class for vector database operations.
    This allows for swapping different vector database implementations.
    """
    
    @abstractmethod
    def initialize(self, **kwargs) -> None:
        """Initialize the vector store with options"""
        pass
    
    @abstractmethod
    def add_documents(self, documents: List[str], metadatas: List[Dict[str, Any]], 
                     ids: List[str], **kwargs) -> None:
        """Add documents to the vector store"""
        pass
    
    @abstractmethod
    def search(self, query: str, filters: Optional[Dict[str, Any]] = None,
              limit: int = 10, **kwargs) -> Dict[str, Any]:
        """Search for documents similar to the query"""
        pass
    
    @abstractmethod
    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a specific document by ID"""
        pass
    
    @abstractmethod
    def delete_document(self, doc_id: str) -> bool:
        """Delete a document from the vector store"""
        pass
    
    @abstractmethod
    def update_document(self, doc_id: str, document: str, 
                       metadata: Dict[str, Any]) -> bool:
        """Update an existing document"""
        pass
    
    @abstractmethod
    def get_collection_stats(self) -> Dict[str, Any]:
        """Get statistics about the collection"""
        pass
    
    @abstractmethod
    def close(self) -> None:
        """Close connections to the vector store"""
        pass

class ChromaDBStore(VectorStore):
    """
    ChromaDB implementation of the VectorStore interface.
    """
    
    def __init__(self):
        self.client = None
        self.collection = None
        self.logger = logging.getLogger(__name__)
        self.embedding_function = None

        # Initialize text chunker with configurable parameters
        self.chunker = TextChunker()

    def _get_embedding_function(self):
        """
        Get an appropriate embedding function based on available dependencies
        """
        print(f'Embedding model config setting: {cfg.VECTOR_EMBEDDING_MODEL}')
        self.logger.info(f'Embedding model config setting: {cfg.VECTOR_EMBEDDING_MODEL}')
        if str(cfg.VECTOR_EMBEDDING_MODEL).lower() in ['default','onnx']:
            # Try to use ONNX-based embeddings
            try:
                # NOTE: No need to test the import anymore - Returning None will automatically default to ONNX as it should
                #import onnxruntime
                #providers = onnxruntime.get_available_providers()
                #print(f"ONNX Runtime loaded successfully with providers: {providers}")

                # Use default ChromaDB embedding (which uses ONNX)
                self.logger.info("Using default ONNX-based embedding function")
                return None  # None means use ChromaDB's default
            except ImportError:
                self.logger.warning("ONNX Runtime not available, using alternative embedding function")
                
        if str(cfg.VECTOR_EMBEDDING_MODEL).lower() in ['default','minilm']:
            # Option 2: Use Sentence Transformers (if available)
            try:
                from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
                self.logger.info("Using SentenceTransformer embedding function")
                ef = SentenceTransformerEmbeddingFunction(
                    model_name="all-MiniLM-L6-v2",
                    device="cpu"
                )
                self.logger.info("Embedding function created successfully")
                return ef
            except Exception as e:
                self.logger.warning(f"SentenceTransformer not available - {str(e)}")

        if str(cfg.VECTOR_EMBEDDING_MODEL).lower() in ['default','openai']:
            # Option 3: Use Sentence Transformers (if available)
            try:
                from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
                self.logger.info("Using OpenAI embedding function")
                ef = OpenAIEmbeddingFunction(
                    api_type='azure',
                    api_key=cfg.AZURE_OPENAI_API_KEY,
                    api_version=cfg.AZURE_OPENAI_API_VERSION,
                    api_base=cfg.AZURE_OPENAI_BASE_URL,
                    deployment_id=cfg.AZURE_OPENAI_DEPLOYMENT_NAME_EMBEDDING,
                    model_name=cfg.AZURE_OPENAI_DEPLOYMENT_NAME_EMBEDDING
                )
                self.logger.info("Embedding function created successfully")
                return ef
            except Exception as e:
                self.logger.warning(f"SentenceTransformer not available - {str(e)}")
        
        if str(cfg.VECTOR_EMBEDDING_MODEL).lower() in ['default','azure']:
            # Option 4: Use OpenAI embeddings (if API key is available)
            try:
                class AzureOpenAIEmbeddingFunction(EmbeddingFunction):
                    def __init__(self):
                        from openai import AzureOpenAI
                        
                        self.client = AzureOpenAI(
                            api_key=cfg.AZURE_OPENAI_API_KEY,
                            api_version=cfg.AZURE_OPENAI_API_VERSION,
                            azure_endpoint=cfg.AZURE_OPENAI_BASE_URL
                        )
                        self.deployment_name = cfg.AZURE_OPENAI_DEPLOYMENT_NAME_EMBEDDING
                        
                    def __call__(self, input: Documents) -> Embeddings:
                        embeddings = []
                        for text in input:
                            response = self.client.embeddings.create(
                                model=self.deployment_name,
                                input=text
                            )
                            embeddings.append(response.data[0].embedding)
                        
                        return embeddings
                    
                    def get_embedding(self, text):
                        response = self.client.embeddings.create(
                                model=self.deployment_name,
                                input=text
                            )
                        return response.data[0].embedding
                    
                self.logger.info("Using Azure OpenAI embedding function")
                return AzureOpenAIEmbeddingFunction()
            except:
                pass
            
        if str(cfg.VECTOR_EMBEDDING_MODEL).lower() in ['default','hash']:
            # Option 5: Create a simple custom embedding function
            self.logger.warning("Using fallback custom embedding function")
            from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
            
            class SimpleHashEmbeddingFunction(EmbeddingFunction):
                """Simple embedding function that doesn't require external dependencies"""
                
                def __call__(self, input: Documents) -> Embeddings:
                    """
                    Generate simple embeddings based on text hashing
                    Returns 384-dimensional vectors to match MiniLM dimension
                    """
                    import hashlib
                    embeddings = []
                    
                    for text in input:
                        # Create a hash of the text
                        text_hash = hashlib.sha512(text.encode()).hexdigest()
                        
                        # Convert hash to a 384-dimensional vector
                        embedding = []
                        for i in range(0, len(text_hash), 2):
                            # Convert each 2 hex chars to a float between -1 and 1
                            hex_val = int(text_hash[i:i+2], 16)
                            normalized_val = (hex_val / 127.5) - 1.0
                            embedding.append(normalized_val)
                        
                        # Pad or truncate to exactly 384 dimensions
                        if len(embedding) < 384:
                            embedding.extend([0.0] * (384 - len(embedding)))
                        else:
                            embedding = embedding[:384]
                        
                        embeddings.append(embedding)
                    
                    return embeddings
            
            return SimpleHashEmbeddingFunction()
    
    def initialize(self, path: str = "./chroma_db", collection_name: str = "documents", 
                  collection_metadata: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        """
        Initialize ChromaDB with the specified path and collection
        
        Args:
            path: Path to ChromaDB storage directory
            collection_name: Name of the collection to use
            collection_metadata: Optional metadata for the collection
            **kwargs: Additional settings for ChromaDB
        """
        try:
            # Get settings from kwargs or use defaults
            settings = kwargs.get('settings', Settings())
            
            # Initialize the client
            self.logger.info("Creating persistent chromadb client...")
            self.client = chromadb.PersistentClient(path=path, settings=settings)
            
            # Get the embedding function
            self.logger.info("Getting embedding function...")
            self.embedding_function = self._get_embedding_function()
            
            # Set up default collection metadata if not provided
            self.logger.info("Setting default collection...")
            if collection_metadata is None:
                collection_metadata = {
                    "description": "Processed documents for analysis and retrieval", 
                    "hnsw:space": "cosine"
                }
            
            # Get or create the collection with the appropriate embedding function
            self.logger.info("Creating collection...")
            if self.embedding_function is not None:
                print("Creating collection with embedding function...")
                self.logger.info("Creating collection with embedding function...")
                self.collection = self.client.get_or_create_collection(
                    name=collection_name,
                    metadata=collection_metadata,
                    embedding_function=self.embedding_function
                )
            else:
                # Use default embedding function
                self.logger.info("Creating default collection...")
                self.collection = self.client.get_or_create_collection(
                    name=collection_name,
                    metadata=collection_metadata
                )
            
            self.logger.info(f"ChromaDB initialized with collection: {collection_name}")
            
        except Exception as e:
            self.logger.error(f"Error initializing ChromaDB: {str(e)}")
            raise
    
    def add_documents(self, documents: List[str], metadatas: List[Dict[str, Any]], 
                     ids: List[str], **kwargs) -> None:
        """
        Add documents to ChromaDB
        
        Args:
            documents: List of document texts
            metadatas: List of metadata dictionaries
            ids: List of unique IDs for the documents
            **kwargs: Additional parameters for ChromaDB add method
        """
        if not self.collection:
            raise ValueError("ChromaDB not initialized. Call initialize() first.")
            
        try:
            print('Adding documents to collection...')
            enable_chunking = cfg.VECTOR_ENABLE_CHUNKING

            all_chunk_texts = []
            all_chunk_metadatas = []
            all_chunk_ids = []

            for doc_text, doc_metadata, doc_id in zip(documents, metadatas, ids):
                if enable_chunking:
                    # Create chunks for this document
                    chunks = self.chunker.chunk_text(doc_text, doc_metadata)
                    
                    for chunk in chunks:
                        chunk_metadata = chunk['metadata'].copy()
                        chunk_metadata.update({
                            'original_doc_id': doc_id,
                            'full_text': doc_text  # Store original text for retrieval
                        })
                        
                        # Create unique chunk ID
                        chunk_id = f"{doc_id}_chunk_{chunk_metadata['chunk_index']}"
                        
                        all_chunk_texts.append(chunk['text'])
                        all_chunk_metadatas.append(chunk_metadata)
                        all_chunk_ids.append(chunk_id)
                        
                    self.logger.debug(f"Created {len(chunks)} chunks for document {doc_id}")
                    print(f"Created {len(chunks)} chunks for document {doc_id}")
                else:
                    # Add document without chunking
                    doc_metadata_copy = doc_metadata.copy()
                    doc_metadata_copy.update({
                        'original_doc_id': doc_id,
                        'full_text': doc_text,
                        'is_chunked': False
                    })
                    
                    all_chunk_texts.append(doc_text)
                    all_chunk_metadatas.append(doc_metadata_copy)
                    all_chunk_ids.append(doc_id)

            # Add all chunks to ChromaDB in batch
            if all_chunk_texts:
                self.collection.add(
                    documents=all_chunk_texts,
                    metadatas=all_chunk_metadatas,
                    ids=all_chunk_ids
                )
                
                self.logger.info(f"Added {len(all_chunk_texts)} chunks to ChromaDB")
                print(f"Added {len(all_chunk_texts)} chunks to ChromaDB")

            self.logger.debug(f"Added {len(documents)} documents to ChromaDB")
            print(f"Added {len(documents)} documents to ChromaDB")
        except Exception as e:
            self.logger.error(f"Error adding documents to ChromaDB: {str(e)}")
            print(f"Error adding documents to ChromaDB: {str(e)}")
            raise
    
    def search(self, query: str, filters: Optional[Dict[str, Any]] = None,
          limit: int = 10, **kwargs) -> Dict[str, Any]:
        """
        Enhanced search that properly handles multiple relevant chunks per document
        """
        if not self.collection:
            raise ValueError("ChromaDB not initialized. Call initialize() first.")
        
        include_full_text = cfg.DOC_INCLUDE_FULL_PAGE_IN_CHUNK_RESULTS
        grouping_strategy = kwargs.get('grouping_strategy', 'best_chunks')  # 'best_chunks', 'merge_chunks', 'separate_docs'
        max_chunks_per_doc = kwargs.get('max_chunks_per_doc', 3)
        
        try:
            print('Searching documents...')
            # Get more results initially to account for grouping
            search_limit = limit * 3  # Get more chunks, then filter/group
            
            collection_count = self.collection.count()
            if collection_count == 0:
                return {"ids": [], "documents": [], "metadatas": [], "distances": []}
            
            actual_search_limit = min(search_limit, collection_count)

            print(86 * '=')
            print('===== ChromaDB Search =====')
            print('Grouping Strategy:')
            print(grouping_strategy)
            print('Search Limit:')
            print(actual_search_limit)
            print('Search Query:')
            print(query)
            print(86 * '-')
            print('Filters:')
            print(filters)

            print(86 * '=')
            
            # Perform the search
            results = self.collection.query(
                query_texts=[query],
                n_results=actual_search_limit,
                where=filters,
                include=["documents", "metadatas", "distances"]
            )
            
            if not results or "ids" not in results or not results["ids"]:
                return {"ids": [], "documents": [], "metadatas": [], "distances": []}
            
            # Process results based on grouping strategy
            if grouping_strategy == 'best_chunks':
                print('Using best chunks...')
                return self._group_by_best_chunks(results, limit, max_chunks_per_doc, include_full_text)
            elif grouping_strategy == 'merge_chunks':
                print('Using merged chunks...')
                return self._group_by_merging_chunks(results, limit, include_full_text)
            elif grouping_strategy == 'separate_docs':
                return self._group_by_separate_documents(results, limit, include_full_text)
            else:
                # Default: return all chunks without grouping
                return self._process_chunks_individually(results, limit, include_full_text)
                
        except Exception as e:
            self.logger.error(f"Error searching ChromaDB: {str(e)}")
            return {"ids": [], "documents": [], "metadatas": [], "distances": []}

    def _group_by_best_chunks(self, results, limit, max_chunks_per_doc, include_full_text):
        """
        Strategy 1: Return multiple best chunks per document (RECOMMENDED)
        """
        # Group chunks by document
        doc_chunks = {}
        
        for i in range(len(results["ids"][0])):
            chunk_id = results["ids"][0][i]
            chunk_text = results["documents"][0][i]
            chunk_metadata = results["metadatas"][0][i]
            distance = results["distances"][0][i]
            
            original_doc_id = chunk_metadata.get('original_doc_id', chunk_id)
            
            if original_doc_id not in doc_chunks:
                doc_chunks[original_doc_id] = []
            
            doc_chunks[original_doc_id].append({
                'chunk_id': chunk_id,
                'chunk_text': chunk_text,
                'metadata': chunk_metadata,
                'distance': distance,
                'score': 1.0 - distance
            })
        
        # Sort chunks within each document by relevance
        for doc_id in doc_chunks:
            doc_chunks[doc_id].sort(key=lambda x: x['score'], reverse=True)
        
        # Build final results with multiple chunks per document
        processed_results = {
            "ids": [],
            "documents": [],
            "metadatas": [],
            "distances": []
        }
        
        # Sort documents by best chunk score
        sorted_docs = sorted(doc_chunks.items(), 
                            key=lambda x: x[1][0]['score'], 
                            reverse=True)
        
        total_results = 0
        for doc_id, chunks in sorted_docs:
            if total_results >= limit:
                break
                
            # Take top chunks from this document
            chunks_to_include = min(max_chunks_per_doc, len(chunks), limit - total_results)
            
            for i in range(chunks_to_include):
                chunk = chunks[i]
                result_metadata = chunk['metadata'].copy()
                
                # Enhanced metadata for multiple chunks
                result_metadata.update({
                    'is_best_chunk_for_doc': (i == 0),
                    'chunk_rank_in_doc': i + 1,
                    'total_relevant_chunks_in_doc': min(len(chunks), max_chunks_per_doc),
                    'document_best_score': chunks[0]['score']
                })

                # Determine what text to return
                if include_full_text and 'full_text' in chunk['metadata']:
                    print('Using Full Text - Length:', len(chunk['metadata']['full_text']))
                    result_text = chunk['metadata']['full_text']
                    result_metadata['matched_chunk'] = chunk['chunk_text']
                    result_metadata['chunk_relevance_score'] = chunk['score']
                else:
                    print('Using Chunk Text - Length:', len(chunk['chunk_text']))
                    result_text = chunk['chunk_text']

                processed_results["ids"].append(chunk['chunk_id'])
                processed_results["documents"].append(result_text)
                processed_results["metadatas"].append(result_metadata)
                processed_results["distances"].append(chunk['distance'])
                
                total_results += 1
                print(86 * '-')

        #print('Processed Results:')
        #print(processed_results)
        
        return processed_results

    def _group_by_merging_chunks(self, results, limit, include_full_text):
        """
        Strategy 2: Merge related chunks from same document into combined results
        """
        # Group chunks by document
        doc_chunks = {}
        
        for i in range(len(results["ids"][0])):
            chunk_id = results["ids"][0][i]
            chunk_text = results["documents"][0][i]
            chunk_metadata = results["metadatas"][0][i]
            distance = results["distances"][0][i]
            
            original_doc_id = chunk_metadata.get('original_doc_id', chunk_id)
            
            if original_doc_id not in doc_chunks:
                doc_chunks[original_doc_id] = {
                    'chunks': [],
                    'best_score': 1.0 - distance,
                    'base_metadata': chunk_metadata
                }
            
            doc_chunks[original_doc_id]['chunks'].append({
                'text': chunk_text,
                'score': 1.0 - distance,
                'chunk_index': chunk_metadata.get('chunk_index', 0)
            })
            
            # Update best score
            if (1.0 - distance) > doc_chunks[original_doc_id]['best_score']:
                doc_chunks[original_doc_id]['best_score'] = 1.0 - distance
        
        # Create merged results
        processed_results = {
            "ids": [],
            "documents": [],
            "metadatas": [],
            "distances": []
        }
        
        # Sort documents by best chunk score
        sorted_docs = sorted(doc_chunks.items(), 
                            key=lambda x: x[1]['best_score'], 
                            reverse=True)[:limit]
        
        for doc_id, doc_data in sorted_docs:
            # Sort chunks by index to maintain order
            chunks = sorted(doc_data['chunks'], key=lambda x: x['chunk_index'])
            
            # Merge chunk texts
            merged_chunks = []
            for chunk in chunks:
                merged_chunks.append(f"[Relevance: {chunk['score']:.2f}] {chunk['text']}")
            
            combined_text = "\n\n".join(merged_chunks)
            
            # Use full text if available
            if include_full_text and 'full_text' in doc_data['base_metadata']:
                result_text = doc_data['base_metadata']['full_text']
                result_metadata = doc_data['base_metadata'].copy()
                result_metadata['matched_chunks_combined'] = combined_text
            else:
                result_text = combined_text
                result_metadata = doc_data['base_metadata'].copy()
            
            result_metadata.update({
                'chunks_merged': len(chunks),
                'best_chunk_score': doc_data['best_score'],
                'all_chunk_scores': [c['score'] for c in chunks]
            })
            
            processed_results["ids"].append(doc_id)
            processed_results["documents"].append(result_text)
            processed_results["metadatas"].append(result_metadata)
            processed_results["distances"].append(1.0 - doc_data['best_score'])

        print('Processed Results:')
        print(processed_results)
        
        return processed_results

    def _group_by_separate_documents(self, results, limit, include_full_text):
        """
        Strategy 3: One result per document (original flawed approach, but sometimes useful)
        """
        seen_docs = set()
        processed_results = {
            "ids": [],
            "documents": [],
            "metadatas": [],
            "distances": []
        }
        
        for i in range(len(results["ids"][0])):
            if len(processed_results["ids"]) >= limit:
                break
                
            chunk_id = results["ids"][0][i]
            chunk_text = results["documents"][0][i]
            chunk_metadata = results["metadatas"][0][i]
            distance = results["distances"][0][i]
            
            original_doc_id = chunk_metadata.get('original_doc_id', chunk_id)
            
            # Skip if we've already seen this document
            if original_doc_id in seen_docs:
                continue
            seen_docs.add(original_doc_id)
            
            # Process as before...
            result_metadata = chunk_metadata.copy()
            
            if include_full_text and 'full_text' in chunk_metadata:
                result_text = chunk_metadata['full_text']
                result_metadata['matched_chunk'] = chunk_text
                result_metadata['chunk_relevance_score'] = 1.0 - distance
            else:
                result_text = chunk_text
            
            processed_results["ids"].append(original_doc_id)
            processed_results["documents"].append(result_text)
            processed_results["metadatas"].append(result_metadata)
            processed_results["distances"].append(distance)
        
        return processed_results

    def _process_chunks_individually(self, results, limit, include_full_text):
        """
        Strategy 4: Return each chunk as separate result (useful for debugging)
        """
        processed_results = {
            "ids": [],
            "documents": [],
            "metadatas": [],
            "distances": []
        }
        
        results_to_process = min(limit, len(results["ids"][0]))
        
        for i in range(results_to_process):
            chunk_id = results["ids"][0][i]
            chunk_text = results["documents"][0][i]
            chunk_metadata = results["metadatas"][0][i]
            distance = results["distances"][0][i]
            
            result_metadata = chunk_metadata.copy()
            result_metadata['is_individual_chunk'] = True
            
            if include_full_text and 'full_text' in chunk_metadata:
                result_text = chunk_metadata['full_text']
                result_metadata['matched_chunk'] = chunk_text
            else:
                result_text = chunk_text
            
            processed_results["ids"].append(chunk_id)
            processed_results["documents"].append(result_text)
            processed_results["metadatas"].append(result_metadata)
            processed_results["distances"].append(distance)
        
        return processed_results
    
    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a specific document by ID, reconstructing from chunks if necessary
        """
        if not self.collection:
            raise ValueError("ChromaDB not initialized. Call initialize() first.")
            
        try:
            # Try to get the document directly first
            result = self.collection.get(ids=[doc_id], include=["documents", "metadatas"])
            
            if result and result["ids"] and len(result["ids"]) > 0:
                return {
                    "id": result["ids"][0],
                    "document": result["documents"][0],
                    "metadata": result["metadatas"][0]
                }
            
            # If not found, try to find chunks for this document
            chunk_results = self.collection.get(
                where={"original_doc_id": doc_id},
                include=["documents", "metadatas"]
            )
            
            if chunk_results and chunk_results["ids"]:
                # Sort chunks by index
                chunks_with_metadata = list(zip(
                    chunk_results["documents"],
                    chunk_results["metadatas"]
                ))
                chunks_with_metadata.sort(key=lambda x: x[1].get('chunk_index', 0))
                
                # Get full text from first chunk metadata (if available)
                first_metadata = chunks_with_metadata[0][1]
                if 'full_text' in first_metadata:
                    full_text = first_metadata['full_text']
                else:
                    # Reconstruct from chunks (less reliable)
                    full_text = ' '.join([chunk[0] for chunk in chunks_with_metadata])
                
                return {
                    "id": doc_id,
                    "document": full_text,
                    "metadata": first_metadata
                }
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error getting document from ChromaDB: {str(e)}")
            return None
    
    def delete_document(self, doc_id: str) -> bool:
        """
        Delete a document and all its chunks from ChromaDB
        """
        if not self.collection:
            raise ValueError("ChromaDB not initialized. Call initialize() first.")
            
        try:
            # Delete the main document
            try:
                 # Delete all chunks for this document
                if cfg.VECTOR_ENABLE_CHUNKING:
                    print(f"Deleting document chunks from ChromaDB for page: {doc_id}")
                    self.logger.info(f"Deleting document chunks from ChromaDB for page: {doc_id}")

                    print('Searching for chunks...')
                    chunk_results = self.collection.get(
                        where={"original_doc_id": doc_id},
                        include=["documents"]
                    )

                    if chunk_results and chunk_results["ids"]:
                        print(f"Chunks found: {str(len(chunk_results['ids']))}")
                        self.collection.delete(ids=chunk_results["ids"])
                        self.logger.debug(f"Deleted {len(chunk_results['ids'])} chunks for document {doc_id}")
                        print(f"Deleted {len(chunk_results['ids'])} chunks for document {doc_id}")
                    else:
                        self.logger.info(f"No chunks found, deleting document from ChromaDB: {doc_id}")
                        print(f"No chunks found, deleting document from ChromaDB: {doc_id}")
                        self.collection.delete(ids=[doc_id])
                        self.logger.info(f"Document deleted from ChromaDB: {doc_id}")
                else:
                    self.logger.info(f"Deleting document from ChromaDB: {doc_id}")
                    print(f"Deleting document from ChromaDB: {doc_id}")
                    self.collection.delete(ids=[doc_id])
                    self.logger.info(f"Document deleted from ChromaDB: {doc_id}")
                    print(f"Document deleted from ChromaDB: {doc_id}")
            except:
                # Document might not exist directly
                self.logger.error(f"Error deleting document from ChromaDB: {str(e)}")
                print(f"Error deleting document from ChromaDB: {str(e)}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error deleting document from ChromaDB: {str(e)}")
            return False
    
    def update_document(self, doc_id: str, document: str, 
                       metadata: Dict[str, Any]) -> bool:
        """
        Update an existing document by deleting old chunks and creating new ones
        """
        if not self.collection:
            raise ValueError("ChromaDB not initialized. Call initialize() first.")
            
        try:
            # Delete existing document and chunks
            self.delete_document(doc_id)
            
            # Add updated document with chunking
            self.add_documents(
                documents=[document],
                metadatas=[metadata],
                ids=[doc_id]
            )
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error updating document in ChromaDB: {str(e)}")
            return False
    
    def get_collection_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the ChromaDB collection
        
        Returns:
            Dictionary with collection statistics
        """
        if not self.collection:
            raise ValueError("ChromaDB not initialized. Call initialize() first.")
            
        try:
            # Get all documents to calculate stats
            results = self.collection.get()
            
            return {
                "count": len(results["ids"]),
                "metadata_fields": list(set().union(*[set(m.keys()) for m in results["metadatas"]])) if results["metadatas"] else []
            }
            
        except Exception as e:
            self.logger.error(f"Error getting ChromaDB collection stats: {str(e)}")
            return {"count": 0, "metadata_fields": []}
    
    def close(self) -> None:
        """Close ChromaDB client connections"""
        # ChromaDB doesn't have an explicit close method in the API
        # This is included for interface compatibility
        self.client = None
        self.collection = None


class LLMDocumentVectorEngine:
    """
    A flexible vector storage and retrieval engine for document embeddings.
    Supports different vector database backends through a common interface.
    """
    
    def __init__(self, vector_store_class: Type[VectorStore] = ChromaDBStore, log_level: str = "INFO"):
        """
        Initialize the document vector engine
        
        Args:
            vector_store_class: Class implementing the VectorStore interface
            log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        """
        # Set up logging
        self.logger = logging.getLogger("DocumentVectorEngine")
        self.logger.setLevel(getattr(logging, log_level))
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
        
        # Initialize vector store
        self.vector_store = vector_store_class()
        self.is_initialized = False
    
    def initialize(self, **kwargs) -> None:
        """
        Initialize the vector store with provided options
        
        Args:
            **kwargs: Implementation-specific options for the vector store
        """
        self.vector_store.initialize(**kwargs)
        self.is_initialized = True
        self.logger.info("Vector store initialized successfully")
    
    def add_document(self, document_text: str, metadata: Dict[str, Any], 
                    doc_id: str) -> bool:
        """
        Add a single document to the vector store
        
        Args:
            document_text: Text content of the document
            metadata: Document metadata
            doc_id: Unique document ID
            
        Returns:
            True if addition was successful, False otherwise
        """
        if not self.is_initialized:
            raise ValueError("Vector engine not initialized. Call initialize() first.")
            
        try:
            self.vector_store.add_documents(
                documents=[document_text],
                metadatas=[metadata],
                ids=[doc_id]
            )
            return True
            
        except Exception as e:
            self.logger.error(f"Error adding document: {str(e)}")
            return False
    
    def add_documents(self, document_batch: List[Dict[str, Any]]) -> bool:
        """
        Add multiple documents to the vector store in a batch
        
        Args:
            document_batch: List of dictionaries with 'text', 'metadata', and 'id' keys
            
        Returns:
            True if batch addition was successful, False otherwise
        """
        if not self.is_initialized:
            raise ValueError("Vector engine not initialized. Call initialize() first.")
            
        try:
            documents = [doc['text'] for doc in document_batch]
            metadatas = [doc['metadata'] for doc in document_batch]
            ids = [doc['id'] for doc in document_batch]
            
            self.vector_store.add_documents(
                documents=documents,
                metadatas=metadatas,
                ids=ids
            )
            return True
            
        except Exception as e:
            self.logger.error(f"Error adding document batch: {str(e)}")
            return False
    
    def search(self, query: str, filters: Optional[Dict[str, Any]] = None, 
              limit: int = 10, min_score: float = 0.0) -> List[Dict[str, Any]]:
        """
        Search for documents similar to the query
        
        Args:
            query: Text query to search for
            filters: Optional metadata filters
            limit: Maximum number of results to return
            min_score: Minimum relevance score (0-1) to include in results
            
        Returns:
            List of search results with document data and relevance scores
        """
        if not self.is_initialized:
            raise ValueError("Vector engine not initialized. Call initialize() first.")
            
        try:
            # Perform search using the vector store
            results = self.vector_store.search(
                query=query,
                filters=filters,
                limit=limit
            )
            print(86 * '%')
            print('Results found...')
            print(results)
            print(86 * '%')

            # Process and enhance the results
            processed_results = []

            # Check if we have nested or flat lists
            if isinstance(results['ids'][0], list):
                # Nested format - legacy code
                ids_list = results['ids'][0]
                docs_list = results['documents'][0]
                meta_list = results['metadatas'][0] 
                dist_list = results['distances'][0]
            else:
                # Flat format - use direct lists
                ids_list = results['ids']
                docs_list = results['documents']
                meta_list = results['metadatas']
                dist_list = results['distances']

            print('Total Results Found:', len(ids_list))
            print('Iterating over results and filtering...')
            for i, (doc_id, document, metadata, distance) in enumerate(zip(
                    ids_list, docs_list, meta_list, dist_list
            )):
                # Calculate relevance score (assuming distance is a similarity metric)
                # This may need adjustment based on the specific vector store/metric used
                relevance_score = 1 - distance
                
                # Skip low-relevance results
                if relevance_score < min_score:
                    continue
                    
                # Create a result entry
                result = {
                    "result_position": i + 1,
                    "relevance_score": relevance_score,
                    "document_id": doc_id,
                    "text": document,
                    "metadata": metadata,
                    "snippet": self._create_snippet(document, query, max_length=300)
                }
                
                processed_results.append(result)

            print('Total Results After Processing:', len(processed_results))
            return processed_results
            
        except Exception as e:
            self.logger.error(f"Error searching documents: {str(e)}")
            return []
    
    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a specific document by ID
        
        Args:
            doc_id: Document ID to retrieve
            
        Returns:
            Document data or None if not found
        """
        if not self.is_initialized:
            raise ValueError("Vector engine not initialized. Call initialize() first.")
            
        try:
            return self.vector_store.get_document(doc_id)
            
        except Exception as e:
            self.logger.error(f"Error retrieving document: {str(e)}")
            return None
    
    def delete_document(self, doc_id: str) -> bool:
        """
        Delete a document from the vector store
        
        Args:
            doc_id: Document ID to delete
            
        Returns:
            True if deletion successful, False otherwise
        """
        if not self.is_initialized:
            raise ValueError("Vector engine not initialized. Call initialize() first.")
            
        try:
            return self.vector_store.delete_document(doc_id)
            
        except Exception as e:
            self.logger.error(f"Error deleting document: {str(e)}")
            return False
    
    def update_document(self, doc_id: str, document_text: str, 
                       metadata: Dict[str, Any]) -> bool:
        """
        Update an existing document
        
        Args:
            doc_id: Document ID to update
            document_text: New document text
            metadata: New metadata
            
        Returns:
            True if update successful, False otherwise
        """
        if not self.is_initialized:
            raise ValueError("Vector engine not initialized. Call initialize() first.")
            
        try:
            return self.vector_store.update_document(
                doc_id=doc_id,
                document=document_text,
                metadata=metadata
            )
            
        except Exception as e:
            self.logger.error(f"Error updating document: {str(e)}")
            return False
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the vector store collection
        
        Returns:
            Dictionary with collection statistics
        """
        if not self.is_initialized:
            raise ValueError("Vector engine not initialized. Call initialize() first.")
            
        try:
            return self.vector_store.get_collection_stats()
            
        except Exception as e:
            self.logger.error(f"Error getting collection stats: {str(e)}")
            return {"error": str(e)}
    
    def close(self) -> None:
        """Close vector store connections"""
        if self.is_initialized:
            self.vector_store.close()
            self.is_initialized = False
            self.logger.info("Vector store connections closed")
    
    def _create_snippet(self, text: str, query: str, max_length: int = 300) -> str:
        """
        Create a relevant text snippet containing the query terms
        
        Args:
            text: Full document text
            query: Search query
            max_length: Maximum snippet length
            
        Returns:
            Text snippet highlighting relevant content
        """
        # Find position of query terms
        query_terms = query.lower().split()
        text_lower = text.lower()
        
        # Find the best position to start the snippet
        best_pos = 0
        max_term_count = 0
        
        # Skip empty text or very short documents
        if not text or len(text) < max_length:
            return text
        
        # Find the most relevant section
        for i in range(len(text) - max_length):
            window = text_lower[i:i+max_length]
            term_count = sum(1 for term in query_terms if term in window)
            
            if term_count > max_term_count:
                max_term_count = term_count
                best_pos = i
        
        # Extract and clean the snippet
        end_pos = min(best_pos + max_length, len(text))
        snippet = text[best_pos:end_pos].strip()
        
        # Add ellipsis if we're not at the beginning/end
        if best_pos > 0:
            snippet = "..." + snippet
        if end_pos < len(text):
            snippet = snippet + "..."
            
        return snippet
