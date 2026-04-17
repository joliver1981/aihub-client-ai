from flask import Flask, request, jsonify, make_response, send_file
from flask_cors import CORS, cross_origin
import os
import json
import time
import uuid
import logging
import tempfile
import traceback
from typing import Dict, Any, Optional, List, Union
from werkzeug.utils import secure_filename
from LLMDocumentEngine import MultiPagePDFHandler, LLMDocumentProcessor
from LLMDocumentSearchEngine import LLMDocumentSearch
import config as cfg
import base64
import anthropic
from api_keys_config import get_anthropic_config, create_anthropic_client
from anthropic_streaming_helper import anthropic_messages_create
import pyodbc
from collections import defaultdict

from request_tracking import RequestTracking
from CommonUtils import AnthropicProxyClient

# Set up logging
from logging.handlers import WatchedFileHandler
logger = logging.getLogger("DocumentAPI")
log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=os.getenv('DOC_API_LOG', os.path.join(os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__))), 'logs', 'doc_api_log.txt')), encoding='utf-8')
handler.setFormatter(formatter)
logger.addHandler(handler)

app = Flask(__name__)

# Configuration
_APP_ROOT = os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__)))
UPLOAD_FOLDER = os.path.join(_APP_ROOT, 'uploads')
ALLOWED_EXTENSIONS = cfg.DOC_ALLOWED_EXTENSIONS # {'pdf', 'docx', 'doc', 'txt', 'csv', 'xls', 'xlsx', 'jpg', 'jpeg', 'png', 'bmp', 'gif', 'tiff', 'tif'}
TEMP_FOLDER = os.path.join(_APP_ROOT, 'temp')

# Create directories if they don't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(TEMP_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

# Document processor instance (initialized lazily)
_document_processor = None
_document_searcher = None
_anthropic_client = None

database_server = cfg.DATABASE_SERVER
database_name = cfg.DATABASE_NAME
username = cfg.DATABASE_UID
password = cfg.DATABASE_PWD


def set_user_request_id(module_name, request_id=None):
    try:
        # Generate or extract request ID
        if not request_id:
            request_id = str(uuid.uuid4())

        # Set in Flask's g object - this is globally accessible for this request only
        RequestTracking.set_tracking(request_id, module_name)

        print(f'Document API: Set request id {request_id} for module {module_name}')
    except Exception as e:
        print(f"Error setting user request id: {str(e)}")


def get_db_connection():
    """Create and return a connection to the database"""
    return pyodbc.connect(f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}")


def get_db_connection_string():
    """Create and return a connection to the database"""
    return f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"


def get_document_types():
    """
    Execute a query to extract all available document types, returning a JSON structure.
    
    Returns:
        str: JSON string with document types
    """
    try:
        # Establish connection to the SQL Server database
        # You may need to adjust these connection parameters for your environment
        conn = get_db_connection()
        
        # Create a cursor
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Execute the query
        query = """
        SELECT distinct d.document_type
        FROM [dbo].[Documents] d
        ORDER BY d.document_type
        """

        print('Running query...')
        cursor.execute(query)
        
        # Use defaultdict to group fields by document type
        document_fields = defaultdict(list)
        
        # Process the results
        for row in cursor.fetchall():
            document_type = row[0]
            document_fields['document_types'].append(document_type)
        
        # Close the connection
        conn.close()
        
        # Convert to regular dict for JSON serialization
        result = dict(document_fields)
        
        # Return as JSON string
        return json.dumps(result, indent=2)
        
    except Exception as e:
        print(str(e))
        return json.dumps({"error": str(e)})
    

def get_document_processor():
    """Lazy initialization of document processor"""
    global _document_processor
    if _document_processor is None:
        logger.info("Initializing document processor...")
        _document_processor = LLMDocumentProcessor(
            sql_connection_string=get_db_connection_string(),
            log_level="INFO"
        )
    return _document_processor


def get_document_searcher():
    """Lazy initialization of document searcher"""
    global _document_searcher
    if _document_searcher is None:
        logger.info("Initializing document searcher...")
        _document_searcher = LLMDocumentSearch(
            sql_connection_string=get_db_connection_string(),
            log_level="INFO"
        )
    return _document_searcher

# Store config globally alongside client
_anthropic_config = None

def get_anthropic_client():
    """Lazy initialization of Anthropic client based on BYOK/config"""
    global _anthropic_client, _anthropic_config
    
    if _anthropic_client is None and _anthropic_config is None:
        _anthropic_config = get_anthropic_config()
        
        if _anthropic_config['use_direct_api']:
            logger.info(f"Initializing direct Anthropic client (source: {_anthropic_config['source']})")
            _anthropic_client = anthropic.Anthropic(api_key=_anthropic_config['api_key'])
        else:
            logger.info("Anthropic direct client not initialized - using proxy")
            _anthropic_client = None
            
    return _anthropic_client


def allowed_file(filename):
    """Check if file has an allowed extension"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


class DocumentProcessor:
    """Extended document processor for API operations"""
    
    @staticmethod
    def process_document(file_path, document_type=None, execution_id=None, 
                         force_ai_extraction=False, use_batch_processing=True, batch_size=3, is_knowledge_document=False, do_not_store=False, extract_fields=True, detect_document_type=True):
        """Process a document file"""
        processor = get_document_processor()
        try:
            # Generate execution ID if not provided
            if execution_id is None:
                execution_id = 0         #str(uuid.uuid4())

            logger.debug(f"Document Processor Params:")
            logger.debug(f"file_path: {file_path}")
            logger.debug(f"force_ai_extraction: {force_ai_extraction}")
            logger.debug(f"document_type: {document_type}")
            logger.debug(f"do_not_store: {do_not_store}")
                
            result = processor.process_document(
                file_path=file_path,
                document_type=document_type,
                force_ai_extraction=force_ai_extraction,
                use_batch_processing=use_batch_processing,
                batch_size=batch_size,
                execution_id=execution_id,
                is_knowledge_document=is_knowledge_document,
                do_not_store=do_not_store,
                extract_fields=extract_fields,
                detect_document_type=detect_document_type
            )

            try:
                document_pages = result["pages"]
                document_text = ""
                extracted_data = []
                for page in document_pages:
                    document_text += page["full_text"] + '\n\n'
                    extracted_data.append(page["extracted_data"])
            except Exception as e:
                print("Failed to extract document text: ", str(e))
                document_text = ""
            
            return {
                "status": "success",
                "message": f"Document processed successfully: {result['filename']}",
                "document_id": result["document_id"],
                "execution_id": execution_id,
                "document_type": result["document_type"],
                "page_count": result["page_count"],
                "document_text": document_text,
                "extracted_data": extracted_data
            }
            
        except Exception as e:
            logger.error(f"Error processing document: {str(e)}")
            logger.error(traceback.format_exc())
            return {
                "status": "error",
                "message": f"Error processing document: {str(e)}",
                "execution_id": execution_id
            }
    
    @staticmethod
    def process_directory(directory_path, document_type=None, 
                          recursive=False, execution_id=None):
        """Process all documents in a directory"""
        processor = get_document_processor()
        try:
            # Generate execution ID if not provided
            if execution_id is None:
                execution_id = str(uuid.uuid4())
                
            results_df = processor.process_directory(
                directory_path=directory_path,
                document_type=document_type,
                recursive=recursive,
                execution_id=execution_id
            )
            
            # Convert DataFrame to list of dictionaries
            results = results_df.to_dict(orient='records')
            
            return {
                "status": "success",
                "message": f"Processed {len(results)} documents",
                "execution_id": execution_id,
                "results": results
            }
            
        except Exception as e:
            logger.error(f"Error processing directory: {str(e)}")
            logger.error(traceback.format_exc())
            return {
                "status": "error",
                "message": f"Error processing directory: {str(e)}",
                "execution_id": execution_id
            }
    
    @staticmethod
    def get_document_data(document_id, format='json'):
        """Get all data for a document"""
        searcher = get_document_searcher()
        try:
            data = searcher.export_document_data(document_id, format=format)
            return {
                "status": "success",
                "data": data
            }
        except Exception as e:
            logger.error(f"Error getting document data: {str(e)}")
            return {
                "status": "error",
                "message": f"Error retrieving document data: {str(e)}"
            }


class DocumentSearcher:
    """Extended document searcher for API operations"""
    
    @staticmethod
    def search_documents(query, document_type=None, filters=None, 
                         n_results=5, min_score=0.0):
        """Search for documents"""
        searcher = get_document_searcher()
        try:
            results = searcher.search_documents(
                query=query,
                document_type=document_type,
                filters=filters,
                n_results=n_results,
                min_score=min_score
            )
            
            return {
                "status": "success",
                "result_count": len(results),
                "results": results
            }
            
        except Exception as e:
            logger.error(f"Error searching documents: {str(e)}")
            return {
                "status": "error",
                "message": f"Error searching documents: {str(e)}"
            }


def document_processor_run_job(job_id):
    """Trigger a job to run immediately"""
    print('Running job...')
    # In a real implementation, this would add the job to a queue
    # or trigger a background process. Here we'll just simulate it.
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # RLS
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        print('Getting job details...')
        # Get the job details
        cursor.execute("SELECT * FROM DocumentJobs WHERE JobID = ?", (job_id,))
        job_row = cursor.fetchone()
            
        # Convert row to dictionary
        columns = [column[0] for column in cursor.description]
        job = dict(zip(columns, job_row))

        # Update the last run time
        cursor.execute("""
            UPDATE DocumentJobs
            SET LastRunAt = getutcdate()
            WHERE JobID = ?
        """, (job_id,))
        
        # Create a new execution record
        cursor.execute("""
            INSERT INTO DocumentJobExecutions (
                JobID, StartedAt, Status, DocumentsProcessed
            ) VALUES (?, getutcdate(), 'QUEUED', 0)
        """, (job_id,))

        conn.commit()

        # Run the job
        # Get the execution ID
        cursor.execute("SELECT @@IDENTITY")
        execution_id = cursor.fetchone()[0]

        cursor.close()
        conn.close()
        print('Job started successfully!', execution_id)
    
    except Exception as e:
        print(f'Error queuing job: {str(e)}')
        conn.rollback()
        cursor.close()
        conn.close()

# API Routes
@app.route('/document/process', methods=['POST'])
@cross_origin()
def process_document_route():
    """Process a document via API"""
    try:
        set_user_request_id('document_processor')

        filepath = request.form.get('filePath')
        
        # If user does not select file, browser also submits an empty part without filename
        if filepath == '':
            return jsonify({
                "status": "error",
                "message": "No selected file"
            }), 400
            
        if filepath and allowed_file(filepath):
            # Save the file to a secure location
            #filename = secure_filename(filepath)
            #file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file_path = filepath
            #file.save(file_path)
            
            # Get parameters from the request
            document_type = request.form.get('document_type')
            execution_id = request.form.get('execution_id')
            force_ai_extraction = request.form.get('force_ai_extraction', 'false').lower() == 'true'
            use_batch_processing = request.form.get('use_batch_processing', 'true').lower() == 'true'
            batch_size = int(request.form.get('batch_size', '3'))
            is_knowledge_document = request.form.get('is_knowledge_document', 'false').lower() == 'true'
            do_not_store = request.form.get('do_not_store', 'false').lower() == 'true'
            extract_fields = request.form.get('extract_fields', 'true').lower() == 'true'
            detect_document_type = request.form.get('detect_document_type', 'true').lower() == 'true'

            logger.debug(f"Processing document do_not_store: {do_not_store}")
            
            # Process the document
            result = DocumentProcessor.process_document(
                file_path=file_path,
                document_type=document_type,
                execution_id=execution_id,
                force_ai_extraction=force_ai_extraction,
                use_batch_processing=use_batch_processing,
                batch_size=batch_size,
                is_knowledge_document=is_knowledge_document,
                do_not_store=do_not_store,
                extract_fields=extract_fields,
                detect_document_type=detect_document_type
            )
            
            return jsonify(result)
        else:
            return jsonify({
                "status": "error",
                "message": f"Invalid file type. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}"
            }), 400
            
    except Exception as e:
        logger.error(f"Error in document processing API: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500


@app.route('/document/process_directory', methods=['POST'])
@cross_origin()
def process_directory_route():
    """Process all documents in a directory"""
    try:
        data = request.json
        if not data or 'directory_path' not in data:
            return jsonify({
                "status": "error",
                "message": "Missing required parameter: directory_path"
            }), 400
            
        directory_path = data['directory_path']
        document_type = data.get('document_type')
        recursive = data.get('recursive', False)
        execution_id = data.get('execution_id')
        
        if not os.path.isdir(directory_path):
            return jsonify({
                "status": "error",
                "message": f"Directory not found: {directory_path}"
            }), 404
            
        result = DocumentProcessor.process_directory(
            directory_path=directory_path,
            document_type=document_type,
            recursive=recursive,
            execution_id=execution_id
        )
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error in directory processing API: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500


@app.route('/document/search', methods=['POST'])
@cross_origin()
def search_documents_route_by_ids():
    """Search for documents with custom filters including document IDs"""
    try:
        set_user_request_id('document_analyzer')

        data = request.json
        if not data:
            return jsonify({
                "status": "error",
                "message": "Missing request data"
            }), 400
            
        query = data.get('query', '')
        filters = data.get('filters', {})
        n_results = int(data.get('n_results', 5))
        min_score = float(data.get('min_score', 0.3))
        
        # Initialize document searcher
        searcher = get_document_searcher()
        
        # Convert filters to ChromaDB format if they're not already
        # Special handling for document_id filters
        chroma_filters = {}
        
        if "document_id" in filters:
            if isinstance(filters["document_id"], dict) and "$in" in filters["document_id"]:
                # It's a list of document IDs
                document_ids = filters["document_id"]["$in"]
                
                # Build a where clause for document IDs
                where_clauses = []
                for doc_id in document_ids:
                    where_clauses.append({"document_id": doc_id})
                
                if len(where_clauses) > 1:
                    chroma_filters = {"$or": where_clauses}
                elif len(where_clauses) == 1:
                    chroma_filters = where_clauses[0]
            else:
                # Single document ID
                chroma_filters = {"document_id": filters["document_id"]}
        else:
            # Use filters as is for other cases
            chroma_filters = filters
        
        # Perform the search
        results = searcher.collection.query(
            query_texts=[query],
            where=chroma_filters,
            n_results=n_results
        )
        
        # Process and enhance the results
        processed_results = []
        
        for i, (doc_id, document, metadata, distance) in enumerate(zip(
                results['ids'][0],
                results['documents'][0], 
                results['metadatas'][0],
                results['distances'][0]
        )):
            # Calculate relevance score
            relevance_score = distance
            
            # Skip low-relevance results
            if relevance_score > min_score:
                continue
                
            # Get additional data from SQL if available
            additional_data = searcher._get_additional_data(doc_id) if searcher.sql_conn else {}
            
            # Create snippet
            snippet = searcher._create_snippet(document, query, max_length=300)
            
            # Create a result entry
            result = {
                "result_position": i + 1,
                "relevance_score": 1 - relevance_score,
                "page_id": doc_id,
                "document_id": metadata.get("document_id", ""),
                "filename": metadata.get("filename", ""),
                "page_number": metadata.get("page_number", 0),
                "document_type": metadata.get("document_type", ""),
                "snippet": snippet,
                "metadata": metadata,
                "extracted_fields": additional_data
            }
            
            processed_results.append(result)
        
        return jsonify({
            "status": "success",
            "result_count": len(processed_results),
            "results": processed_results
        })
        
    except Exception as e:
        logger.error(f"Error searching documents: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500


@app.route('/document/get/<document_id>', methods=['GET'])
@cross_origin()
def get_document_route(document_id):
    """Get document data"""
    try:
        format_type = request.args.get('format', 'json')
        if format_type not in ['json', 'csv', 'dataframe']:
            return jsonify({
                "status": "error",
                "message": f"Invalid format: {format_type}. Allowed values: json, csv, dataframe"
            }), 400
            
        result = DocumentProcessor.get_document_data(
            document_id=document_id,
            format=format_type
        )
        
        if format_type == 'csv' and result['status'] == 'success':
            # Create a response with CSV content
            response = make_response(result['data'])
            response.headers['Content-Disposition'] = f'attachment; filename={document_id}.csv'
            response.headers['Content-Type'] = 'text/csv'
            return response
        else:
            return jsonify(result)
            
    except Exception as e:
        logger.error(f"Error getting document data: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500


@app.route('/document/save', methods=['POST'])
@cross_origin()
def save_document_route():
    """Save processed document content to a file"""
    try:
        print('Saving document...')
        data = request.json
        print('Data:')
        print(data)

        if not data or 'content' not in data or 'outputPath' not in data:
            return jsonify({
                "status": "error",
                "message": "Missing required parameters: content and outputPath"
            }), 400
        
        content = data['content']
        output_path = data['outputPath']

        # Handle both formats: JSON string with 'document_text' key or direct text
        try:
            content_json = json.loads(content)
            # If it's a dict with 'document_text' key, extract it
            if isinstance(content_json, dict) and 'document_text' in content_json:
                document_text = content_json['document_text']
            else:
                # If it's valid JSON but not in expected format, convert back to string
                document_text = content
        except (json.JSONDecodeError, TypeError):
            # If it's not valid JSON, treat it as plain text
            document_text = content

        print('=====>>>>> DOCUMENT TEXT:')
        print(document_text)
        
        # Ensure the directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # Write content to file
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(document_text)
            
        return jsonify({
            "status": "success",
            "message": f"Document saved to {output_path}",
            "path": output_path
        })
        
    except Exception as e:
        logger.error(f"Error saving document: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500
    

@app.route('/document/extract', methods=['POST'])
@cross_origin()
def extract_document_fields_route():
    """Process a document via API"""
    try:
        set_user_request_id('document_processor')

        filepath = request.form.get('filePath')
        print('Extracting file', filepath)
        # If user does not select file, browser also submits an empty part without filename
        if filepath == '':
            return jsonify({
                "status": "error",
                "message": "No selected file"
            }), 400
            
        if filepath and allowed_file(filepath):
            file_path = filepath
            
            # Get parameters from the request
            document_type = request.form.get('document_type')
            execution_id = request.form.get('execution_id')
            force_ai_extraction = True
            use_batch_processing = request.form.get('use_batch_processing', 'true').lower() == 'true'
            batch_size = int(request.form.get('batch_size', '3'))
            do_not_store = request.form.get('do_not_store', 'false').lower() == 'true'
            extract_fields = request.form.get('extract_fields', 'true').lower() == 'true'
            detect_document_type = request.form.get('detect_document_type', 'true').lower() == 'true'

            print('Processing document for extraction...')
            
            # Process the document
            result = DocumentProcessor.process_document(
                file_path=file_path,
                document_type=document_type,
                execution_id=execution_id,
                force_ai_extraction=force_ai_extraction,
                use_batch_processing=use_batch_processing,
                batch_size=batch_size,
                do_not_store=do_not_store,
                extract_fields=extract_fields,
                detect_document_type=detect_document_type
            )
            # return {
            #     "status": "success",
            #     "message": f"Document processed successfully: {result['filename']}",
            #     "document_id": result["document_id"],
            #     "execution_id": execution_id,
            #     "document_type": result["document_type"],
            #     "page_count": result["page_count"],
            #     "document_text": document_text,
            #     "extracted_data": extracted_data
            # } result['extracted_data']   result['filename']

            print('Raw results', result)

            return_result = {
                "status": "success",
                "message": f"Document processed successfully: {filepath}",
                "extracted_data": result['extracted_data']
                }
            
            print('Return results', return_result)
            
            return jsonify(return_result)
        else:
            return jsonify({
                "status": "error",
                "message": f"Invalid file type. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}"
            }), 400
            
    except Exception as e:
        logger.error(f"Error in document processing API: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500


@app.route('/document/extract_text', methods=['POST'])
@cross_origin()
def extract_document_text_route():
    """Process a document via API"""
    try:
        set_user_request_id('document_processor')

        filepath = request.form.get('filePath')
        print('Extracting file', filepath)
        # If user does not select file, browser also submits an empty part without filename
        if filepath == '':
            return jsonify({
                "status": "error",
                "message": "No selected file"
            }), 400
            
        if filepath and allowed_file(filepath):
            file_path = filepath
            
            # Get parameters from the request
            document_type = request.form.get('document_type')
            execution_id = request.form.get('execution_id')
            force_ai_extraction = True
            use_batch_processing = request.form.get('use_batch_processing', 'true').lower() == 'true'
            batch_size = int(request.form.get('batch_size', '3'))
            do_not_store = request.form.get('do_not_store', 'false').lower() == 'true'
            extract_fields = request.form.get('extract_fields', 'true').lower() == 'true'
            detect_document_type = request.form.get('detect_document_type', 'true').lower() == 'true'

            print('Processing document for extraction...')
            
            # Process the document
            result = DocumentProcessor.process_document(
                file_path=file_path,
                document_type=document_type,
                execution_id=execution_id,
                force_ai_extraction=force_ai_extraction,
                use_batch_processing=use_batch_processing,
                batch_size=batch_size,
                do_not_store=do_not_store,
                extract_fields=extract_fields,
                detect_document_type=detect_document_type
            )

            print('Raw results', result)

            return_result = {
                "status": "success",
                "message": f"Document processed successfully: {filepath}",
                "text": result['document_text']
                }
            
            print('Return results', return_result)
            
            return jsonify(return_result)
        else:
            return jsonify({
                "status": "error",
                "message": f"Invalid file type. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}"
            }), 400
            
    except Exception as e:
        logger.error(f"Error in document processing API: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500


@app.route('/document/analyze', methods=['POST'])
@cross_origin()
def analyze_document_route():
    """Analyze a document with a specific AI prompt"""
    try:
        set_user_request_id('document_analyzer')
        # # Check if the post request has the file part
        # if 'file' not in request.files:
        #     return jsonify({
        #         "status": "error",
        #         "message": "No file part in the request"
        #     }), 400
            
        # file = request.files['file']
        
        # if file.filename == '':
        #     return jsonify({
        #         "status": "error",
        #         "message": "No selected file"
        #     }), 400
            
        if 'prompt' not in request.form:
            return jsonify({
                "status": "error",
                "message": "Missing required parameter: prompt"
            }), 400
            
        prompt = request.form['prompt']
        
        filepath = request.form.get('filePath')
        print('Checking file', filepath)

        # If user does not select file, browser also submits an empty part without filename
        if filepath == '':
            return jsonify({
                "status": "error",
                "message": "No selected file"
            }), 400
            
        if filepath and allowed_file(filepath):
            # Save the file to a temporary location
            #filename = secure_filename(file.filename)
            #temp_file_path = os.path.join(TEMP_FOLDER, filename)
            #file.save(temp_file_path)
            temp_file_path = filepath
            
            # Get file content and encode for Claude
            with open(temp_file_path, "rb") as f:
                file_content = f.read()
                file_base64 = base64.b64encode(file_content).decode("utf-8")
            
            # Get parameters
            model = request.form.get('model', cfg.ANTHROPIC_MODEL)
            system_prompt = request.form.get('system_prompt', 
                "You are an expert document analyst. Analyze the document and provide detailed information based on the user's request.")
            
            # Call Claude API
            from CommonUtils import AnthropicProxyClient

            _config = get_anthropic_config()

            if _config['use_direct_api']:
                # Direct API call
                client = get_anthropic_client()
                response = client.messages.create(
                    model=model,
                    max_tokens=int(cfg.ANTHROPIC_MAX_TOKENS),
                    system=system_prompt,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "document",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "application/pdf",
                                        "data": file_base64
                                    }
                                },
                                {
                                    "type": "text", 
                                    "text": prompt
                                }
                            ]
                        }
                    ]
                )
                analysis_text = response.content[0].text
            else:
                # Use proxy
                proxy_client = AnthropicProxyClient()
                response = proxy_client.messages_create(
                    model=model,
                    max_tokens=int(cfg.ANTHROPIC_MAX_TOKENS),
                    system=system_prompt,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "document",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "application/pdf",
                                        "data": file_base64
                                    }
                                },
                                {
                                    "type": "text", 
                                    "text": prompt
                                }
                            ]
                        }
                    ]
                )
                analysis_text = response['content'][0]['text']
            
            # Clean up temporary file
            #os.remove(temp_file_path)
            
            return jsonify({
                "status": "success",
                "filename": filepath,
                "prompt": prompt,
                "analysis": analysis_text
            })
            
        else:
            return jsonify({
                "status": "error",
                "message": f"Invalid file type. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}"
            }), 400
            
    except Exception as e:
        logger.error(f"Error analyzing document: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500


# Add these endpoints to your Flask app to support workflow variables

@app.route('/save/workflow/variables/<workflow_id>', methods=['POST'])
@cross_origin()
def save_workflow_variables(workflow_id):
    """Save workflow variables for a specific workflow"""
    try:
        data = request.json
        
        if not data:
            return jsonify({
                "status": "error",
                "message": "Missing variable data"
            }), 400
            
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Execute stored procedure to set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # First delete existing variables for this workflow
        cursor.execute("""
            DELETE FROM Workflow_Variables
            WHERE workflow_id = ?
        """, workflow_id)
        
        # Then insert new variables
        for variable in data.get('variables', []):
            cursor.execute("""
                INSERT INTO Workflow_Variables (workflow_id, variable_name, variable_type, default_value, description)
                VALUES (?, ?, ?, ?, ?)
            """, workflow_id, variable['name'], variable['type'], variable['defaultValue'], variable['description'])
        
        conn.commit()
        conn.close()
        
        return jsonify({
            "status": "success",
            "message": f"Variables saved for workflow {workflow_id}"
        })
        
    except Exception as e:
        logger.error(f"Error saving workflow variables: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error saving variables: {str(e)}"
        }), 500


@app.route('/get/workflow/variables/<workflow_id>', methods=['GET'])
@cross_origin()
def get_workflow_variables(workflow_id):
    """Get workflow variables for a specific workflow"""
    try:
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Execute stored procedure to set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get variables for this workflow
        cursor.execute("""
            SELECT variable_name, variable_type, default_value, description
            FROM Workflow_Variables
            WHERE workflow_id = ?
        """, workflow_id)
        
        variables = []
        for row in cursor.fetchall():
            variables.append({
                "name": row[0],
                "type": row[1],
                "defaultValue": row[2],
                "description": row[3]
            })
        
        conn.close()
        
        return jsonify({
            "status": "success",
            "workflow_id": workflow_id,
            "variables": variables
        })
        
    except Exception as e:
        logger.error(f"Error getting workflow variables: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error getting variables: {str(e)}"
        }), 500


@app.route('/document/types', methods=['GET'])
@cross_origin()
def get_document_types_route():
    """Get available document types"""
    try:
        #searcher = get_document_searcher()
        #types = list(searcher.schemas.keys())
        types = get_document_types()
        
        return jsonify({
            "status": "success",
            "document_types": types
        })
        
    except Exception as e:
        logger.error(f"Error getting document types: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500


@app.route('/document/health', methods=['GET'])
@cross_origin()
def health_check():
    """API health check endpoint"""
    return jsonify({
        "status": "ok",
        "message": "Document API is operational",
        "timestamp": time.time()
    })


@app.route('/document/reprocess-vectors', methods=['POST'])
@cross_origin()
def reprocess_vectors_route():
    """
    API endpoint to reprocess document vectors from existing database data.
    
    Request body (JSON):
    {
        "document_id": "optional_document_id",
        "document_type": "optional_document_type", 
        "batch_size": 100,
        "force_recreate": false
    }
    """
    try:
        # Parse request data
        data = request.get_json() or {}
        
        document_id = data.get('document_id')
        document_type = data.get('document_type')
        batch_size = data.get('batch_size', 100)
        force_recreate = data.get('force_recreate', False)
        
        # Validate batch_size
        if not isinstance(batch_size, int) or batch_size < 1 or batch_size > 1000:
            return jsonify({
                "status": "error",
                "message": "batch_size must be an integer between 1 and 1000"
            }), 400
        
        # Validate force_recreate
        if not isinstance(force_recreate, bool):
            return jsonify({
                "status": "error", 
                "message": "force_recreate must be a boolean"
            }), 400
            
        logger.info(f"Starting vector reprocessing with params: document_id={document_id}, document_type={document_type}, batch_size={batch_size}, force_recreate={force_recreate}")
        
        # Initialize document engine
        doc_engine = get_document_processor()
        
        # Call the reprocessing function
        result = doc_engine.reprocess_document_vectors(
            document_id=document_id,
            document_type=document_type,
            batch_size=batch_size,
            force_recreate=force_recreate
        )
        
        # Return results
        status_code = 200
        if result["status"] == "error":
            status_code = 500
        elif result["status"] == "partial_success":
            status_code = 207  # Multi-status
            
        return jsonify(result), status_code
        
    except Exception as e:
        logger.error(f"Error in vector reprocessing API: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}",
            "pages_processed": 0,
            "documents_processed": 0,
            "errors": [str(e)]
        }), 500


@app.route('/document/reprocess-vectors/all', methods=['POST'])
@cross_origin()
def reprocess_all_vectors_route():
    """
    API endpoint to reprocess ALL document vectors.
    
    Request body (JSON):
    {
        "batch_size": 50,
        "force_recreate": false
    }
    """
    try:
        data = request.get_json() or {}
        batch_size = data.get('batch_size', 50)
        force_recreate = data.get('force_recreate', False)
        
        # Validate inputs
        if not isinstance(batch_size, int) or batch_size < 1 or batch_size > 1000:
            return jsonify({
                "status": "error",
                "message": "batch_size must be an integer between 1 and 1000"
            }), 400
            
        if not isinstance(force_recreate, bool):
            return jsonify({
                "status": "error",
                "message": "force_recreate must be a boolean"
            }), 400
        
        logger.info(f"Starting full vector reprocessing with batch_size={batch_size}, force_recreate={force_recreate}")
        
        # Initialize document engine
        doc_engine = get_document_processor()
        
        # Call the reprocessing function for all documents
        result = doc_engine.reprocess_all_vectors(
            batch_size=batch_size,
            force_recreate=force_recreate
        )
        
        # Return results
        status_code = 200
        if result["status"] == "error":
            status_code = 500
        elif result["status"] == "partial_success":
            status_code = 207
            
        return jsonify(result), status_code
        
    except Exception as e:
        logger.error(f"Error in full vector reprocessing API: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}",
            "pages_processed": 0,
            "documents_processed": 0,
            "errors": [str(e)]
        }), 500


# Import the agent communication blueprint
from document_summarization_routes import doc_summ_bp
app.register_blueprint(doc_summ_bp)

