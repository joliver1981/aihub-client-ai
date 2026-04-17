import config as cfg
import os
import json
import logging
from logging.handlers import WatchedFileHandler
import yaml
import pyodbc
from typing import List, Dict, Any, Optional, Tuple, Union
import pandas as pd
import chromadb

from LLMDocumentVectorAdapter import LLMDocumentVectorAdapter
from CommonUtils import rotate_logs_on_startup, get_log_path


# Configure logging
def setup_logging():
    """Configure logging"""
    logger = logging.getLogger("LLMDocumentSearchEngine")
    log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
    log_level = getattr(logging, log_level_name, logging.DEBUG)
    logger.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler = WatchedFileHandler(filename=os.getenv('LLM_DOCUMENT_SEARCH_ENGINE', get_log_path('llm_document_search_engine_log.txt')), encoding='utf-8')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger


rotate_logs_on_startup(os.getenv('LLM_DOCUMENT_SEARCH_ENGINE', get_log_path('llm_document_search_engine_log.txt')))


logger = setup_logging()


database_server = cfg.DATABASE_SERVER
database_name = cfg.DATABASE_NAME
username = cfg.DATABASE_UID
password = cfg.DATABASE_PWD


class LLMDocumentSearch:
    """
    A flexible system to process various document types using Claude Vision,
    extract structured data, and store in vector and relational databases.
    """
    
    def __init__(
        self, 
        vector_db_path: str = "./chroma_db",
        schema_dir: str = "./schemas",
        sql_connection_string: Optional[str] = f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}",
        log_level: str = "DEBUG"
    ):
        """
        Initialize the document processor with necessary configurations.
        
        Args:
            vector_db_path: Path to store ChromaDB
            schema_dir: Directory containing document schemas
            sql_connection_string: Connection string for SQL Server (optional)
            log_level: Logging level
        """
        # Set up logging
        self.logger = logger
        self.logger.setLevel(getattr(logging, log_level))
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
        
        # Initialize vector database
        # self.chroma_client = chromadb.PersistentClient(path=vector_db_path)
        # self.collection = self.chroma_client.get_or_create_collection(
        #     name="documents",
        #     metadata={"description": "Processed documents for analysis and retrieval", "hnsw:space": "cosine"}
        # )

        # Initialize vector database using the adapter
        self.vector_adapter = LLMDocumentVectorAdapter(
            use_remote=True,  # Change to True to use remote API
            vector_db_path=vector_db_path,
            collection_name="documents",
            log_level=log_level
        )
        # For backward compatibility with existing code
        self.collection = self.vector_adapter
        
        # Initialize SQL connection (if provided)
        self.sql_connection_string = sql_connection_string
        self.sql_conn = None
        if sql_connection_string:
            try:
                self.sql_conn = pyodbc.connect(sql_connection_string)
                self.logger.info("Connected to SQL Server database")
                self._ensure_database_tables()
            except Exception as e:
                self.logger.error(f"Failed to connect to SQL database: {str(e)}")
        
        # Load document schemas
        self.schema_dir = schema_dir
        self.schemas = self._load_schemas()
        
    def _load_schemas(self) -> Dict[str, Any]:
        """
        Load document schemas from the schema directory.
        
        Returns:
            Dictionary of document types and their schemas
        """
        schemas = {}
        
        # Create schema directory if it doesn't exist
        os.makedirs(self.schema_dir, exist_ok=True)
        
        # Load each schema file
        for filename in os.listdir(self.schema_dir):
            if filename.endswith(('.yaml', '.yml')):
                try:
                    with open(os.path.join(self.schema_dir, filename), 'r') as f:
                        schema = yaml.safe_load(f)
                        doc_type = schema.get('document_type')
                        if doc_type:
                            schemas[doc_type] = schema
                            self.logger.info(f"Loaded schema for document type: {doc_type}")
                except Exception as e:
                    self.logger.error(f"Error loading schema {filename}: {str(e)}")
                    
        return schemas
    
    def _ensure_database_tables(self):
        """Create necessary database tables if they don't exist"""
        if not self.sql_conn:
            return
            
        cursor = self.sql_conn.cursor()
        
        # Create documents table if it doesn't exist
        try:
            # Main documents table
            cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'Documents')
            BEGIN
                CREATE TABLE Documents (
                    document_id VARCHAR(100) PRIMARY KEY,
                    filename VARCHAR(255) NOT NULL,
                    original_path VARCHAR(1000) NOT NULL,
                    document_type VARCHAR(100) NOT NULL,
                    page_count INT NOT NULL,
                    processed_at DATETIME NOT NULL,
                    archived_path VARCHAR(1000),
                    hash_value VARCHAR(100)
                )
            END
            """)
            
            # Document pages table
            cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'DocumentPages')
            BEGIN
                CREATE TABLE DocumentPages (
                    page_id VARCHAR(100) PRIMARY KEY,
                    document_id VARCHAR(100) NOT NULL,
                    page_number INT NOT NULL,
                    full_text NVARCHAR(MAX),
                    vector_id VARCHAR(100),
                    FOREIGN KEY (document_id) REFERENCES Documents(document_id)
                )
            END
            """)
            
            # Document fields table (to store extracted key/value pairs)
            cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'DocumentFields')
            BEGIN
                CREATE TABLE DocumentFields (
                    field_id INT IDENTITY(1,1) PRIMARY KEY,
                    page_id VARCHAR(100) NOT NULL,
                    field_name NVARCHAR(255) NOT NULL,
                    field_value NVARCHAR(MAX),
                    field_path NVARCHAR(500), -- JSON path to nested fields
                    confidence FLOAT,
                    FOREIGN KEY (page_id) REFERENCES DocumentPages(page_id)
                )
            END
            """)
            
            self.sql_conn.commit()
            self.logger.info("Database tables created or verified")
            
        except Exception as e:
            self.logger.error(f"Error creating database tables: {str(e)}")
            self.sql_conn.rollback()

 
    def _extract_with_schema(self, text: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract structured data using a predefined schema.
        
        Args:
            text: Extracted text from a document page
            schema: Schema definition for the document type
            
        Returns:
            Dictionary of structured fields
        """
        extracted_data = {}
        
        # Get field definitions from schema
        fields = schema.get('fields', {})
        
        # Extract each field using patterns defined in schema
        for field_name, field_info in fields.items():
            pattern = field_info.get('pattern')
            
            if pattern:
                value = self._extract_field(text, pattern)
                
                # Apply any transformations defined in the schema
                transform = field_info.get('transform')
                if transform == 'to_number' and value:
                    try:
                        value = float(value.replace(',', ''))
                    except ValueError:
                        pass
                elif transform == 'to_date' and value:
                    # Keep as string but ensure consistent format if possible
                    pass
                
                # Handle nested fields using dot notation in field name
                if '.' in field_name:
                    parts = field_name.split('.')
                    current = extracted_data
                    for part in parts[:-1]:
                        if part not in current:
                            current[part] = {}
                        current = current[part]
                    current[parts[-1]] = value
                else:
                    extracted_data[field_name] = value
        
        return extracted_data
    
    
    def _extract_field(self, text: str, pattern: str) -> str:
        """Extract a single field using regex pattern"""
        import re
        match = re.search(pattern, text)
        return match.group(1).strip() if match else ""
    
    def _extract_all_fields(self, text: str, pattern: str) -> List[str]:
        """Extract all occurrences of a field using regex pattern"""
        import re
        matches = re.findall(pattern, text)
        return [match.strip() for match in matches if match.strip()]
    

    def search_documents(
        self, 
        query: str, 
        document_type: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        n_results: int = 5,
        min_score: float = 0.0
    ) -> List[Dict[str, Any]]:
        """
        Search for documents using vector similarity and metadata filters.
        
        Args:
            query: Text query to search for
            document_type: Optional document type to filter by
            filters: Optional additional metadata filters
            n_results: Maximum number of results to return
            min_score: Minimum relevance score (0-1) to include in results
            
        Returns:
            List of search results with references to original documents
        """
        # Set up where clause for filtering
        where_clause = {}
        
        # Build filter conditions
        conditions = []
        
        # Add document_type filter if provided
        if document_type:
            conditions.append({"document_type": document_type})
        
        # Add other filters if provided
        if filters:
            for key, value in filters.items():
                conditions.append({key: value})
        
        # Build the final where clause
        if len(conditions) > 1:
            # Multiple conditions
            where_clause = {"$and": conditions}
        elif len(conditions) == 1:
            # Single condition
            where_clause = conditions[0]
        
        try:
            print('Performing vector search...')
            # Perform the search using the adapter
            results = self.vector_adapter.query(
                query_texts=[query],
                where=where_clause if where_clause else None,
                n_results=n_results,
                min_score=min_score
            )
            print('Vector search results:')
            print(results)
            
            # Process and enhance the results
            processed_results = []
            
            for i, (doc_id, document, metadata, distance) in enumerate(zip(
                    results['ids'][0],
                    results['documents'][0], 
                    results['metadatas'][0],
                    results['distances'][0]
            )):
                # Calculate relevance score
                relevance_score = 1 - distance
                
                # Skip low-relevance results
                if relevance_score < min_score:
                    continue
                    
                # Get additional data from SQL if available
                additional_data = self._get_additional_data(doc_id) if self.sql_conn else {}
                
                # Create a result entry
                result = {
                    "result_position": i + 1,
                    "relevance_score": relevance_score,
                    "page_id": doc_id,
                    "document_id": metadata.get("document_id", ""),
                    "filename": metadata.get("filename", ""),
                    "page_number": metadata.get("page_number", 0),
                    "document_type": metadata.get("document_type", ""),
                    "snippet": self._create_snippet(document, query, max_length=200),
                    "metadata": metadata,
                    "extracted_fields": additional_data
                }
                
                processed_results.append(result)
                
            return processed_results
            
        except Exception as e:
            self.logger.error(f"Error searching documents: {str(e)}")
            return []
    
    
    def search_documents_legacy(
        self, 
        query: str, 
        document_type: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        n_results: int = 5,
        min_score: float = 0.0
    ) -> List[Dict[str, Any]]:
        """
        Search for documents using vector similarity and metadata filters.
        
        Args:
            query: Text query to search for
            document_type: Optional document type to filter by
            filters: Optional additional metadata filters
            n_results: Maximum number of results to return
            min_score: Minimum relevance score (0-1) to include in results
            
        Returns:
            List of search results with references to original documents
        """
        # Set up where clause for filtering
        where_clause = {}
        
        # ChromaDB expects the where clause to be in a specific format for multiple conditions
        # We need to use $and operator for multiple conditions
        conditions = []
        
        # Add document_type filter if provided
        if document_type:
            conditions.append({"document_type": document_type})
        
        # Add other filters if provided
        if filters:
            for key, value in filters.items():
                conditions.append({key: value})
        
        # Build the final where clause
        if len(conditions) > 1:
            # Multiple conditions use $and operator
            where_clause = {"$and": conditions}
        elif len(conditions) == 1:
            # Single condition can be used directly
            where_clause = conditions[0]
        
        print(86 * '-')
        print(query)
        print(where_clause)
        print(86 * '-')
        print('Running collection query...')
        # Perform the search
        try:
            results = self.collection.query(
                query_texts=[query],
                where=where_clause if where_clause else None,
                #where_document={"$contains":query},
                n_results=n_results
            )
        except:
            print('Catastrophic error detected in chromadb, exiting...')
            results = None
            processed_results = []
            return processed_results
        print('Done running query.')
        # Process and enhance the results
        processed_results = []
        
        for i, (doc_id, document, metadata, distance) in enumerate(zip(
                results['ids'][0],
                results['documents'][0], 
                results['metadatas'][0],
                results['distances'][0]
        )):
            # Calculate relevance score
            #relevance_score = math.exp(-float(distance))   #  For Euclidean
            #relevance_score = 1 - distance
            relevance_score = distance

            print('=============================================')
            print('distance', distance)
            print('relevance_score', relevance_score)
            print('min_score', min_score)
            print('=============================================')
            
            # Skip low-relevance results
            if relevance_score > min_score:
                continue
                
            # Get additional data from SQL if available
            additional_data = self._get_additional_data(doc_id) if self.sql_conn else {}
            
            # Create a result entry
            result = {
                "result_position": i + 1,
                "relevance_score": 1 - relevance_score,
                "page_id": doc_id,
                "document_id": metadata.get("document_id", ""),
                "filename": metadata.get("filename", ""),
                "page_number": metadata.get("page_number", 0),
                "document_type": metadata.get("document_type", ""),
                "snippet": self._create_snippet(document, query, max_length=200),
                "metadata": metadata,
                "extracted_fields": additional_data
            }
            
            processed_results.append(result)
            
        return processed_results
    
    def _get_additional_data(self, page_id: str) -> Dict[str, Any]:
        """
        Get additional data from SQL database for a document page.
        
        Args:
            page_id: Page ID
            
        Returns:
            Dictionary of field data
        """
        result = {}
        
        try:
            cursor = self.sql_conn.cursor()
            
            # Query fields for this page
            cursor.execute(
                "SELECT field_name, field_value, field_path FROM DocumentFields WHERE page_id = ?", 
                (page_id,)
            )
            
            rows = cursor.fetchall()
            
            # Organize fields by path
            for field_name, field_value, field_path in rows:
                # Handle nested paths
                if '.' in field_path:
                    # Build nested structure
                    parts = field_path.split('.')
                    current = result
                    for part in parts[:-1]:
                        # Handle array indices in path
                        if '[' in part and ']' in part:
                            base_name = part.split('[')[0]
                            index = int(part.split('[')[1].split(']')[0])
                            if base_name not in current:
                                current[base_name] = []
                            # Ensure list is long enough
                            while len(current[base_name]) <= index:
                                current[base_name].append({})
                            current = current[base_name][index]
                        else:
                            if part not in current:
                                current[part] = {}
                            current = current[part]
                    current[parts[-1]] = field_value
                else:
                    # Add top-level field
                    result[field_name] = field_value
                    
        except Exception as e:
            self.logger.error(f"Error retrieving additional data: {str(e)}")
            
        return result
    
    def _create_snippet(self, text: str, query: str, max_length: int = 200) -> str:
        """Create a relevant text snippet containing the query terms"""
        # Find position of query terms
        query_terms = query.lower().split()
        text_lower = text.lower()
        
        # Find the best position to start the snippet
        best_pos = 0
        max_term_count = 0
        
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
    

    def search_documents_by_ids(
        self, 
        query: str, 
        document_ids: list,
        n_results: int = 5,
        min_score: float = 0.3
    ):
        """
        Search for documents by IDs using vector similarity.
        
        Args:
            query: Text query to search for
            document_ids: List of document IDs to search within
            n_results: Maximum number of results to return
            min_score: Minimum relevance score (0-1) to include in results
            
        Returns:
            List of search results with references to original documents
        """
        # Set up where clause for filtering by document IDs
        where_clause = {}
        
        # Create document_id filter
        if document_ids:
            # Chroma requires using $in operator for array values
            where_clause = {"document_id": {"$in": document_ids}}
        else:
            # If no document IDs provided, return empty results
            return []
        
        try:
            # Perform the search using the adapter
            results = self.vector_adapter.query(
                query_texts=[query],
                where=where_clause,
                n_results=n_results,
                min_score=min_score
            )
            
            # Process and enhance the results (similar to regular search_documents)
            processed_results = []
            
            for i, (doc_id, document, metadata, distance) in enumerate(zip(
                    results['ids'][0],
                    results['documents'][0], 
                    results['metadatas'][0],
                    results['distances'][0]
                )):
                # Calculate relevance score
                relevance_score = 1 - distance
                
                # Skip low-relevance results
                if relevance_score < min_score:
                    continue
                    
                # Get additional data from SQL if available
                additional_data = self._get_additional_data(doc_id) if self.sql_conn else {}
                
                # Create a result entry
                result = {
                    "result_position": i + 1,
                    "relevance_score": relevance_score,
                    "page_id": doc_id,
                    "document_id": metadata.get("document_id", ""),
                    "filename": metadata.get("filename", ""),
                    "page_number": metadata.get("page_number", 0),
                    "document_type": metadata.get("document_type", ""),
                    "snippet": self._create_snippet(document, query, max_length=300),
                    "metadata": metadata,
                    "extracted_fields": additional_data
                }
                
                processed_results.append(result)
                
            return processed_results
            
        except Exception as e:
            self.logger.error(f"Error searching documents by IDs: {str(e)}")
            return []
    
    def search_documents_by_ids_legacy(
        self, 
        query: str, 
        document_ids: list,
        n_results: int = 5,
        min_score: float = 0.3
        ):
        """
        Search for documents by IDs using vector similarity.
        
        Args:
            query: Text query to search for
            document_ids: List of document IDs to search within
            n_results: Maximum number of results to return
            min_score: Minimum relevance score (0-1) to include in results
            
        Returns:
            List of search results with references to original documents
        """
        # Set up where clause for filtering by document IDs
        where_clause = {}
        
        # Create document_id filter
        if document_ids:
            # Chroma requires using $in operator for array values
            where_clause = {"document_id": {"$in": document_ids}}
        else:
            # If no document IDs provided, return empty results
            return []
        
        # Perform the search
        results = self.collection.query(
            query_texts=[query],
            where=where_clause,
            n_results=n_results
        )
        
        # Process and enhance the results (similar to regular search_documents)
        processed_results = []
        
        for i, (doc_id, document, metadata, distance) in enumerate(zip(
                results['ids'][0],
                results['documents'][0], 
                results['metadatas'][0],
                results['distances'][0]
            )):
            # Calculate relevance score (lower distance = higher relevance)
            relevance_score = distance
            
            # Skip low-relevance results
            if relevance_score > min_score:
                continue
                
            # Get additional data from SQL if available
            additional_data = self._get_additional_data(doc_id) if self.sql_conn else {}
            
            # Create a result entry
            result = {
                "result_position": i + 1,
                "relevance_score": 1 - relevance_score,
                "page_id": doc_id,
                "document_id": metadata.get("document_id", ""),
                "filename": metadata.get("filename", ""),
                "page_number": metadata.get("page_number", 0),
                "document_type": metadata.get("document_type", ""),
                "snippet": self._create_snippet(document, query, max_length=300),
                "metadata": metadata,
                "extracted_fields": additional_data
            }
            
            processed_results.append(result)
            
        return processed_results
    
    def export_document_data(self, document_id: str, format: str = 'json') -> Union[str, pd.DataFrame]:
        """
        Export all data for a document in the specified format.
        
        Args:
            document_id: Document ID
            format: Output format ('json', 'csv', or 'dataframe')
            
        Returns:
            String (JSON/CSV) or DataFrame containing document data
        """
        if not self.sql_conn:
            raise ValueError("SQL connection is required for data export")
            
        try:
            cursor = self.sql_conn.cursor()
            
            # Get document info
            cursor.execute(
                "SELECT * FROM Documents WHERE document_id = ?", 
                (document_id,)
            )
            doc_row = cursor.fetchone()
            
            if not doc_row:
                raise ValueError(f"Document ID {document_id} not found")
                
            # Convert row to dict (PYODBC doesn't have column names in row)
            columns = [column[0] for column in cursor.description]
            doc_data = dict(zip(columns, doc_row))
            
            # Get pages
            cursor.execute(
                "SELECT * FROM DocumentPages WHERE document_id = ? ORDER BY page_number", 
                (document_id,)
            )
            pages = []
            for row in cursor.fetchall():
                columns = [column[0] for column in cursor.description]
                page_data = dict(zip(columns, row))
                
                # Get fields for this page
                cursor.execute(
                    "SELECT field_name, field_value, field_path FROM DocumentFields WHERE page_id = ?", 
                    (page_data['page_id'],)
                )
                
                fields = {}
                for field_name, field_value, field_path in cursor.fetchall():
                    fields[field_path] = field_value
                    
                page_data['extracted_fields'] = fields
                pages.append(page_data)
                
            # Combine all data
            full_data = {
                **doc_data,
                'pages': pages
            }
            
            # Return in requested format
            if format == 'json':
                return json.dumps(full_data, indent=2)
            elif format in ('csv', 'dataframe'):
                # Flatten the data for CSV/DataFrame
                rows = []
                for page in pages:
                    base_row = {
                        'document_id': doc_data['document_id'],
                        'filename': doc_data['filename'],
                        'document_type': doc_data['document_type'],
                        'page_number': page['page_number'],
                        'page_id': page['page_id']
                    }
                    
                    # Add fields as columns
                    for field_path, value in page['extracted_fields'].items():
                        # Sanitize column name
                        col_name = field_path.replace('[', '_').replace(']', '').replace('.', '_')
                        base_row[col_name] = value
                        
                    rows.append(base_row)
                    
                df = pd.DataFrame(rows)
                
                if format == 'csv':
                    return df.to_csv(index=False)
                return df
                
            else:
                raise ValueError(f"Unsupported format: {format}")
                
        except Exception as e:
            self.logger.error(f"Error exporting document data: {str(e)}")
            raise
    
    def close(self):
        """Close all connections"""
        if self.sql_conn:
            self.sql_conn.close()
            self.sql_conn = None
            self.logger.info("SQL connection closed")
        
        # Close vector adapter connections
        if hasattr(self, 'vector_adapter'):
            self.vector_adapter.close()
            self.logger.info("Vector adapter connections closed")

    def debug_database_contents(self):
        """Print information about what's in the database"""
        # Get vector database stats through the adapter
        stats = self.vector_adapter.get_collection_stats() if hasattr(self.vector_adapter, 'get_collection_stats') else {}
        
        print(f"Total documents in collection: {stats.get('count', 'unknown')}")
        print(f"Available metadata fields: {stats.get('metadata_fields', [])}")

# Example usage
def main():
    # Initialize the processor
    processor = LLMDocumentSearch()

    try:
        # Check DB content
        #processor.debug_database_contents()
        minimum_score = 0.99
        num_results = 3

        search_query = "GEES MILLS ROAD"
        search_results = processor.search_documents(
            query=search_query,
            document_type="bill_of_lading",
            #filters={"filename": "Amazon 02-27.pdf"},
            min_score=minimum_score,
            n_results=num_results
        )
        print('SEARCH RESULTS 1:')
        #print(search_results)
        print(f"Found {len(search_results)} relevant documents")

        for result in search_results:
            print(f"Document ID: {result['document_id']}")
            print(f"Document: {result['filename']}, Page: {result['page_number']}")
            print(f"Relevance: {result['relevance_score']:.2f}")
            print(f"Snippet: {result['snippet']}")
            print("-" * 50)
        
            # Export document data
            # json_data = processor.export_document_data(result['document_id'], format='json')
            # with open(f"{result['document_id']}_export.json", "w") as f:
            #     f.write(json_data)
            
    finally:
        # Close connections
        processor.close()

if __name__ == "__main__":
    main()