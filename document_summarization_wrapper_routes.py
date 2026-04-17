# document_summarization_routes.py
# Flask blueprint for document summarization proxy routes
# Import this into your main app.py file

from flask import Blueprint, request, jsonify, make_response, render_template, redirect, url_for, flash
from flask_cors import cross_origin
from flask_login import login_required
import requests
import logging
import config as cfg
from CommonUtils import get_document_api_base_url

# Create the blueprint
summarization_bp = Blueprint('summarization', __name__)

# Get the root logger (matches your app.py logging setup)
logger = logging.getLogger()

def log_request_details():
    """Helper function to log comprehensive request details for debugging."""
    try:
        print(f"Request method: {request.method}")
        print(f"Request URL: {request.url}")
        print(f"Content-Type: {request.content_type}")
        print(f"Is JSON: {request.is_json}")
        print(f"Has JSON: {request.json is not None}")
        print(f"Form data: {dict(request.form)}")
        print(f"Args: {dict(request.args)}")
        if request.is_json:
            print(f"JSON data: {request.json}")
        if request.data:
            print(f"Raw data: {request.data}")
    except Exception as e:
        print(str(e))

def make_doc_api_request(method, endpoint, data=None, params=None, timeout=300):
    """
    Make a request to the document API.
    
    Args:
        method: HTTP method (GET, POST, DELETE, etc.)
        endpoint: API endpoint path (without leading slash)
        data: Request body data
        params: Query parameters
        timeout: Request timeout in seconds
        
    Returns:
        Flask Response object
    """
    try:
        base_url = get_document_api_base_url()
        url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        
        print(f"Making {method} request to document API: {url}")
        if data:
            print(f"Request data: {data}")
        if params:
            print(f"Request params: {params}")
        
        # Prepare headers
        headers = {
            'Accept': 'application/json'
        }
        
        # For requests with data, set Content-Type to application/json
        if data is not None:
            headers['Content-Type'] = 'application/json'
        
        # Make the request
        response = requests.request(
            method=method,
            url=url,
            json=data,
            params=params,
            timeout=timeout,
            headers={'Content-Type': 'application/json'}
        )
        
        print(f"Document API response: {method} {url} - Status: {response.status_code}")
        
        # Create Flask response with same status code and content
        flask_response = make_response(response.content, response.status_code)
        
        # Copy relevant headers
        for header_name, header_value in response.headers.items():
            if header_name.lower() not in ['content-encoding', 'content-length', 'transfer-encoding', 'connection']:
                flask_response.headers[header_name] = header_value
        
        return flask_response
        
    except requests.exceptions.Timeout:
        logger.error(f"Timeout calling document API: {url} (timeout: {timeout}s)")
        return jsonify({
            "status": "error",
            "message": f"Request timeout after {timeout} seconds",
            "service": "document_api"
        }), 504
        
    except requests.exceptions.ConnectionError:
        logger.error(f"Connection error to document API: {url}")
        return jsonify({
            "status": "error",
            "message": "Unable to connect to document processing service",
            "service": "document_api"
        }), 503
        
    except Exception as e:
        logger.error(f"Error calling document API {url}: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Internal error: {str(e)}",
            "service": "document_api"
        }), 500

# ============================================================================
# CORE DOCUMENT SUMMARIZATION PROXY ROUTES
# ============================================================================

@summarization_bp.route('/api/documents/<document_id>/summarize', methods=['POST'])
@cross_origin()
def proxy_summarize_document_pages(document_id):
    """
    Generate summaries for all pages of a document.
    Proxies to app_doc_api.py route.
    """
    print(f"Proxying document summarization request for document: {document_id}")
    logger.info(f"Proxying document summarization request for document: {document_id}")
    print('Logging request details...')
    
    # Log request details for debugging
    if logger.isEnabledFor(logging.DEBUG):
        log_request_details()

    print('Checking if JSON or form data...')
    # Handle both JSON and form data
    if request.is_json and request.json:
        data = request.json
        print(f"Using JSON data: {data}")
    elif request.form:
        # Convert form data to JSON format
        data = {
            'summary_types': request.form.getlist('summary_types') or ['standard'],
            'custom_instructions': request.form.get('custom_instructions', '').strip() or None,
            'overwrite_existing': request.form.get('overwrite_existing', 'false').lower() == 'true'
        }
        print(f"Converted form data to JSON: {data}")
        print(f"Raw summary_types: {request.form.get('summary_types', '')}")
        print(f"Raw summary_types list: {request.form.getlist('summary_types')}")
    elif request.args:
        # Handle query parameters as fallback
        data = {
            'summary_types': request.args.getlist('summary_types') or ['standard'],
            'custom_instructions': request.args.get('custom_instructions', '').strip() or None,
            'overwrite_existing': request.args.get('overwrite_existing', 'false').lower() == 'true'
        }
        print(f"Converted query args to JSON: {data}")
    else:
        # Default data
        data = {
            'summary_types': ['standard'],
            'custom_instructions': None,
            'overwrite_existing': False
        }
        print(f"No request data found, using defaults: {data}")
    
    # Filter out empty values
    if data.get('custom_instructions') == '':
        data['custom_instructions'] = None
    
    print(f"Final request data: {data}")
    
    return make_doc_api_request(
        method='POST',
        endpoint=f'api/documents/{document_id}/summarize',
        data=data,
        timeout=600  # Longer timeout for summarization
    )

@summarization_bp.route('/api/pages/<page_id>/summarize', methods=['POST'])
@cross_origin()
def proxy_summarize_single_page(page_id):
    """
    Generate summaries for a single page.
    Proxies to app_doc_api.py route.
    """
    logger.info(f"Proxying page summarization request for page: {page_id}")
    
    # Handle both JSON and form data
    if request.is_json and request.json:
        data = request.json
        logger.debug(f"Received JSON data: {data}")
    else:
        # Convert form data to JSON format
        data = {
            'summary_types': request.form.getlist('summary_types') or request.args.getlist('summary_types') or ['standard'],
            'custom_instructions': request.form.get('custom_instructions') or request.args.get('custom_instructions'),
            'overwrite_existing': (request.form.get('overwrite_existing', 'false').lower() == 'true') or 
                                (request.args.get('overwrite_existing', 'false').lower() == 'true')
        }
        logger.debug(f"Converted form data to: {data}")

    print(86 * '-')
    print('Single page data:', data)
    print(86 * '-')
    
    return make_doc_api_request(
        method='POST',
        endpoint=f'api/pages/{page_id}/summarize',
        data=data,
        timeout=300
    )

@summarization_bp.route('/api/documents/<document_id>/summaries', methods=['GET'])
@cross_origin()
def proxy_get_document_summaries(document_id):
    """
    Retrieve all summaries for a document.
    Proxies to app_doc_api.py route.
    """
    logger.debug(f"Proxying get document summaries request for document: {document_id}")
    print(f"Proxying get document summaries request for document: {document_id}")
    print('Request.args')
    print(request.args.to_dict())
    return make_doc_api_request(
        method='GET',
        endpoint=f'api/documents/{document_id}/summaries',
        params=request.args.to_dict()
    )

@summarization_bp.route('/api/pages/<page_id>/summaries', methods=['GET'])
@cross_origin()
def proxy_get_page_summaries(page_id):
    """
    Retrieve all summaries for a specific page.
    Proxies to app_doc_api.py route.
    """
    logger.debug(f"Proxying get page summaries request for page: {page_id}")
    
    return make_doc_api_request(
        method='GET',
        endpoint=f'api/pages/{page_id}/summaries',
        params=request.args.to_dict()
    )

@summarization_bp.route('/api/summaries/<int:summary_id>', methods=['DELETE'])
@cross_origin()
def proxy_delete_summary(summary_id):
    """
    Delete a specific summary by ID.
    Proxies to app_doc_api.py route.
    """
    logger.info(f"Proxying delete summary request for summary ID: {summary_id}")
    
    return make_doc_api_request(
        method='DELETE',
        endpoint=f'api/summaries/{summary_id}'
    )

@summarization_bp.route('/api/summaries/batch-regenerate', methods=['POST'])
@cross_origin()
def proxy_batch_regenerate_summaries():
    """
    Batch regenerate summaries for multiple documents or pages.
    Proxies to app_doc_api.py route.
    """
    # Handle both JSON and form data
    if request.is_json and request.json:
        request_data = request.json
        logger.debug(f"Received JSON data: {request_data}")
    else:
        # Convert form data to JSON format
        request_data = {
            'document_ids': request.form.getlist('document_ids') or request.args.getlist('document_ids') or [],
            'page_ids': request.form.getlist('page_ids') or request.args.getlist('page_ids') or [],
            'summary_types': request.form.getlist('summary_types') or request.args.getlist('summary_types') or ['standard'],
            'custom_instructions': request.form.get('custom_instructions') or request.args.get('custom_instructions'),
            'overwrite_existing': (request.form.get('overwrite_existing', 'false').lower() == 'true') or 
                                (request.args.get('overwrite_existing', 'false').lower() == 'true')
        }
        logger.debug(f"Converted form data to: {request_data}")
    
    doc_count = len(request_data.get('document_ids', []))
    page_count = len(request_data.get('page_ids', []))
    
    logger.info(f"Proxying batch regenerate summaries request: {doc_count} documents, {page_count} pages")
    
    return make_doc_api_request(
        method='POST',
        endpoint='api/summaries/batch-regenerate',
        data=request_data,
        timeout=1800  # 30 minutes for batch operations
    )

@summarization_bp.route('/api/summaries/config/length-limits', methods=['GET'])
@cross_origin()
def proxy_get_summary_length_limits():
    """
    Get current summary length limits configuration.
    Proxies to app_doc_api.py route.
    """
    logger.debug("Proxying get summary length limits request")
    
    return make_doc_api_request(
        method='GET',
        endpoint='api/summaries/config/length-limits'
    )

@summarization_bp.route('/api/summaries/config/length-limits', methods=['POST'])
@cross_origin()
def proxy_update_summary_length_limits():
    """
    Update summary length limits for the current session.
    Proxies to app_doc_api.py route.
    """
    logger.info("Proxying update summary length limits request")
    
    # Handle both JSON and form data
    if request.is_json and request.json:
        data = request.json
        logger.debug(f"Received JSON data: {data}")
    else:
        # Convert form data to JSON format
        data = {}
        
        # Handle length limits
        length_limits = {}
        for summary_type in ['brief', 'standard', 'detailed', 'bullet_points', 'executive']:
            limit_value = request.form.get(f'length_limits_{summary_type}') or request.args.get(f'length_limits_{summary_type}')
            if limit_value:
                try:
                    length_limits[summary_type] = int(limit_value)
                except ValueError:
                    pass
        
        if length_limits:
            data['length_limits'] = length_limits
        
        # Handle global max length
        global_max = request.form.get('global_max_length') or request.args.get('global_max_length')
        if global_max:
            try:
                data['global_max_length'] = int(global_max)
            except ValueError:
                pass
        
        # Handle enforce limits
        enforce = request.form.get('enforce_limits') or request.args.get('enforce_limits')
        if enforce is not None:
            data['enforce_limits'] = enforce.lower() == 'true'
        
        logger.debug(f"Converted form data to: {data}")
    
    return make_doc_api_request(
        method='POST',
        endpoint='api/summaries/config/length-limits',
        data=data
    )

@summarization_bp.route('/api/summaries/stats/length-analysis', methods=['GET'])
@cross_origin()
def proxy_get_summary_length_analysis():
    """
    Get statistical analysis of summary lengths.
    Proxies to app_doc_api.py route.
    """
    logger.debug("Proxying get summary length analysis request")
    
    return make_doc_api_request(
        method='GET',
        endpoint='api/summaries/stats/length-analysis',
        params=request.args.to_dict()
    )

# ============================================================================
# MAIN APP INTEGRATION ROUTES
# ============================================================================

@summarization_bp.route('/documents/<document_id>/summarize-quick', methods=['POST'])
@login_required
def quick_document_summarize(document_id):
    """
    Quick summarize route with standard options - designed for your main app workflow.
    """
    try:
        logger.info(f"Quick summarization requested for document: {document_id}")
        
        # Get form data or JSON data
        if request.is_json:
            data = request.json or {}
        else:
            data = {
                'summary_types': request.form.getlist('summary_types') or ['standard'],
                'custom_instructions': request.form.get('custom_instructions', ''),
                'overwrite_existing': request.form.get('overwrite_existing', 'false').lower() == 'true'
            }
        
        # Set defaults
        if not data.get('summary_types'):
            data['summary_types'] = ['standard']
        
        logger.debug(f"Quick summarization data: {data}")
        
        # Make request to document API
        response = make_doc_api_request(
            method='POST',
            endpoint=f'api/documents/{document_id}/summarize',
            data=data,
            timeout=600
        )
        
        # If it's a successful JSON response, add flash messages
        if response.status_code == 200:
            try:
                result = response.get_json()
                if result and result.get('status') == 'success':
                    stats = result.get('summary_statistics', {})
                    success_count = stats.get('successful_summaries', 0)
                    total_pages = stats.get('total_pages', 0)
                    
                    logger.info(f"Successfully generated {success_count} summaries for {total_pages} pages")
                    flash(f"Successfully generated {success_count} summaries for {total_pages} pages", 'success')
                else:
                    message = result.get('message', 'Unknown issue') if result else 'Unknown issue'
                    logger.warning(f"Summarization completed with warnings: {message}")
                    flash(f"Summarization completed with warnings: {message}", 'warning')
            except Exception as json_error:
                logger.debug(f"Could not parse JSON response: {str(json_error)}")
                # If we can't parse JSON, just return the response as-is
                pass
        else:
            logger.error(f"Summarization failed with status {response.status_code}")
        
        return response
        
    except Exception as e:
        logger.error(f"Error in quick_document_summarize for document {document_id}: {str(e)}")
        flash(f"Error generating summaries: {str(e)}", 'error')
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@summarization_bp.route('/documents/<document_id>/summaries-view')
@login_required
def view_document_summaries(document_id):
    """
    View summaries for a document in your main app interface.
    """
    try:
        logger.info(f"Viewing summaries for document: {document_id}")
        
        # Get summaries from document API
        response = make_doc_api_request(
            method='GET',
            endpoint=f'api/documents/{document_id}/summaries',
            params=request.args.to_dict()
        )
        
        if response.status_code == 200:
            summaries_data = response.get_json()
            
            # Get document info (you may need to implement this endpoint in app_doc_api.py)
            doc_response = make_doc_api_request(
                method='GET',
                endpoint=f'api/documents/{document_id}/info'
            )
            
            document_info = {}
            if doc_response.status_code == 200:
                document_info = doc_response.get_json()
                logger.debug(f"Retrieved document info for {document_id}")
            else:
                logger.warning(f"Could not retrieve document info for {document_id}")
            
            return render_template('document_summaries.html',
                                 document_id=document_id,
                                 document_info=document_info,
                                 summaries=summaries_data)
        else:
            error_data = response.get_json() if response.content else {}
            error_message = error_data.get('message', 'Unknown error')
            logger.error(f"Error loading summaries for document {document_id}: {error_message}")
            flash(f"Error loading summaries: {error_message}", 'error')
            return redirect(url_for('document_view', document_id=document_id))
            
    except Exception as e:
        logger.error(f"Error in view_document_summaries for document {document_id}: {str(e)}")
        flash(f"Error loading summaries: {str(e)}", 'error')
        return redirect(url_for('document_view', document_id=document_id))

@summarization_bp.route('/admin/summarization-dashboard')
@login_required
def summarization_admin_dashboard():
    """
    Admin dashboard for summarization statistics and management.
    """
    try:
        logger.info("Loading summarization admin dashboard")
        
        # Get length analysis
        analysis_response = make_doc_api_request(
            method='GET',
            endpoint='api/summaries/stats/length-analysis'
        )
        
        analysis_data = {}
        if analysis_response.status_code == 200:
            analysis_data = analysis_response.get_json()
            logger.debug("Successfully loaded length analysis data")
        else:
            logger.warning("Failed to load length analysis data")
        
        # Get configuration
        config_response = make_doc_api_request(
            method='GET',
            endpoint='api/summaries/config/length-limits'
        )
        
        config_data = {}
        if config_response.status_code == 200:
            config_data = config_response.get_json()
            logger.debug("Successfully loaded summarization configuration")
        else:
            logger.warning("Failed to load summarization configuration")
        
        return render_template('admin_summarization_dashboard.html',
                             analysis=analysis_data,
                             config=config_data)
                             
    except Exception as e:
        logger.error(f"Error in summarization_admin_dashboard: {str(e)}")
        flash(f"Error loading dashboard: {str(e)}", 'error')
        return redirect(url_for('admin_dashboard'))

@summarization_bp.route('/api/documents/batch-summarize', methods=['POST'])
@cross_origin()
def batch_summarize_documents():
    """
    Batch summarize multiple documents - wrapper for easier integration.
    """
    try:
        # Handle both JSON and form data
        if request.is_json and request.json:
            data = request.json
            logger.debug(f"Received JSON data: {data}")
        else:
            # Convert form data to JSON format
            data = {
                'document_ids': request.form.getlist('document_ids') or request.args.getlist('document_ids') or [],
                'summary_types': request.form.getlist('summary_types') or request.args.getlist('summary_types') or ['standard'],
                'custom_instructions': request.form.get('custom_instructions') or request.args.get('custom_instructions'),
                'overwrite_existing': (request.form.get('overwrite_existing', 'false').lower() == 'true') or 
                                    (request.args.get('overwrite_existing', 'false').lower() == 'true')
            }
            logger.debug(f"Converted form data to: {data}")
        
        document_ids = data.get('document_ids', [])
        summary_types = data.get('summary_types', ['standard'])
        
        logger.info(f"Batch summarization requested for {len(document_ids)} documents")
        
        if not document_ids:
            logger.warning("Batch summarization requested but no document IDs provided")
            return jsonify({
                "status": "error",
                "message": "No document IDs provided"
            }), 400
        
        # Prepare request for the document API
        batch_data = {
            "document_ids": document_ids,
            "summary_types": summary_types,
            "custom_instructions": data.get('custom_instructions'),
            "overwrite_existing": data.get('overwrite_existing', False)
        }
        
        logger.debug(f"Batch summarization data: {batch_data}")
        
        return make_doc_api_request(
            method='POST',
            endpoint='api/summaries/batch-regenerate',
            data=batch_data,
            timeout=1800
        )
        
    except Exception as e:
        logger.error(f"Error in batch_summarize_documents: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def check_document_api_health():
    """
    Check if the document API service is running.
    Use this in your main app to verify connectivity.
    """
    try:
        response = make_doc_api_request(
            method='GET',
            endpoint='health',
            timeout=5
        )
        is_healthy = response.status_code == 200
        logger.debug(f"Document API health check: {'healthy' if is_healthy else 'unhealthy'}")
        return is_healthy
    except Exception as e:
        logger.warning(f"Document API health check failed: {str(e)}")
        return False

def get_summarization_status(document_id):
    """
    Get summarization status for a document.
    Returns summary statistics for display in your main app.
    """
    try:
        logger.debug(f"Getting summarization status for document: {document_id}")
        
        response = make_doc_api_request(
            method='GET',
            endpoint=f'api/documents/{document_id}/summaries'
        )
        
        if response.status_code == 200:
            data = response.get_json()
            total_pages = data.get('total_pages_with_summaries', 0)
            
            summary_types = list(set([
                summary_type 
                for page in data.get('pages', [])
                for summary_type in page.get('summaries', {}).keys()
            ]))
            
            logger.debug(f"Document {document_id} has {total_pages} pages with summaries")
            
            return {
                'has_summaries': total_pages > 0,
                'total_pages_with_summaries': total_pages,
                'summary_types': summary_types,
                'status': 'success'
            }
        else:
            logger.warning(f"Failed to get summarization status for document {document_id}")
            return {
                'has_summaries': False,
                'status': 'error',
                'message': 'Failed to get status'
            }
            
    except Exception as e:
        logger.error(f"Error getting summarization status for document {document_id}: {str(e)}")
        return {
            'has_summaries': False,
            'status': 'error',
            'message': str(e)
        }

# ============================================================================
# TEMPLATE CONTEXT PROCESSOR
# ============================================================================

@summarization_bp.app_context_processor
def inject_summarization_helpers():
    """
    Inject summarization helper functions into template context.
    """
    def document_has_summaries(document_id):
        """Template helper to check if document has summaries."""
        status = get_summarization_status(document_id)
        return status.get('has_summaries', False)
    
    def get_summary_count(document_id):
        """Template helper to get summary count for a document."""
        status = get_summarization_status(document_id)
        return status.get('total_pages_with_summaries', 0)
    
    def get_summary_types(document_id):
        """Template helper to get available summary types for a document."""
        status = get_summarization_status(document_id)
        return status.get('summary_types', [])
    
    def doc_api_available():
        """Template helper to check if document API is available."""
        return check_document_api_health()
    
    return {
        'document_has_summaries': document_has_summaries,
        'get_summary_count': get_summary_count,
        'get_summary_types': get_summary_types,
        'doc_api_available': doc_api_available
    }

# ============================================================================
# BLUEPRINT-SPECIFIC ERROR HANDLERS
# ============================================================================

@summarization_bp.errorhandler(503)
def handle_service_unavailable(error):
    """Handle service unavailable errors (document API down)."""
    logger.error("Document API service unavailable (503)")
    return jsonify({
        "status": "error",
        "message": "Document processing service is currently unavailable",
        "service": "document_api",
        "retry_after": 30
    }), 503

@summarization_bp.errorhandler(504)
def handle_gateway_timeout(error):
    """Handle gateway timeout errors (document API timeout)."""
    logger.error("Document API request timeout (504)")
    return jsonify({
        "status": "error",
        "message": "Document processing request timed out",
        "service": "document_api",
        "suggestion": "Try processing fewer documents at once"
    }), 504

# ============================================================================
# HEALTH CHECK ROUTE
# ============================================================================

@summarization_bp.route('/api/summarization/health', methods=['GET'])
def summarization_health_check():
    """
    Health check endpoint for summarization services.
    """
    try:
        doc_api_healthy = check_document_api_health()
        
        health_status = {
            "service": "document_summarization_proxy",
            "status": "healthy" if doc_api_healthy else "degraded",
            "document_api": "healthy" if doc_api_healthy else "unhealthy",
            "timestamp": str(logger.handlers[0].formatter.formatTime(logger.makeRecord(
                "health", logging.INFO, "", 0, "", (), None
            ))) if logger.handlers else None
        }
        
        status_code = 200 if doc_api_healthy else 503
        
        logger.info(f"Summarization health check: {health_status['status']}")
        
        return jsonify(health_status), status_code
        
    except Exception as e:
        logger.error(f"Error in summarization health check: {str(e)}")
        return jsonify({
            "service": "document_summarization_proxy",
            "status": "error",
            "message": str(e)
        }), 500

# ============================================================================
# EXPORT THE BLUEPRINT
# ============================================================================

# The blueprint is ready to be imported into your main app.py
# Add this line to your app.py:
# from document_summarization_routes import summarization_bp
# app.register_blueprint(summarization_bp)

"""
USAGE IN YOUR MAIN APP.PY:

# At the top of your app.py file:
from document_summarization_routes import summarization_bp

# After creating your Flask app:
app.register_blueprint(summarization_bp)

# You can now use the utility functions throughout your app:
from document_summarization_routes import get_summarization_status, check_document_api_health

@app.route('/documents/<document_id>')
@login_required
def document_view(document_id):
    # Your existing document view logic
    document = get_document(document_id)  # Your existing function
    
    # Add summary status
    summary_status = get_summarization_status(document_id)
    
    return render_template('document_view.html',
                         document=document,
                         summary_status=summary_status)

# Template usage:
# {% if document_has_summaries(document.id) %}
#     <a href="{{ url_for('summarization.view_document_summaries', document_id=document.id) }}">
#         View Summaries ({{ get_summary_count(document.id) }} pages)
#     </a>
# {% else %}
#     <form method="post" action="{{ url_for('summarization.quick_document_summarize', document_id=document.id) }}">
#         <button type="submit" class="btn btn-primary">Generate Summaries</button>
#     </form>
# {% endif %}
"""