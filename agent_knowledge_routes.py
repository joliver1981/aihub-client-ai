from flask import render_template, request, jsonify
import os
import uuid
import logging
import requests
import json
from flask_login import login_required, current_user
from flask_cors import cross_origin
from CommonUtils import get_db_connection, get_db_connection_string, get_document_api_base_url, get_base_url

# Function to get agent knowledge items
def get_agent_knowledge(agent_id):
    """Get knowledge items associated with an agent"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get knowledge items
        cursor.execute("""
            SELECT ak.knowledge_id, ak.agent_id, ak.document_id, ak.description, ak.added_date, 
                   d.filename, d.document_type, d.page_count
            FROM AgentKnowledge ak
            JOIN Documents d ON ak.document_id = d.document_id
            WHERE ak.agent_id = ? AND ak.is_active = 1
                    AND ISNULL(ak.added_by, 'USER') = 'USER'
            ORDER BY ak.added_date DESC
        """, agent_id)
        
        # Format results
        knowledge_items = []
        for row in cursor.fetchall():
            knowledge_items.append({
                'knowledge_id': row[0],
                'agent_id': row[1],
                'document_id': row[2],
                'description': row[3],
                'added_date': row[4].strftime('%Y-%m-%d %H:%M:%S') if row[4] else None,
                'filename': row[5],
                'document_type': row[6],
                'page_count': row[7]
            })

        try:
            # Get knowledge items for this user if available
            cursor.execute("""
                SELECT ak.knowledge_id, ak.agent_id, ak.document_id, ak.description, ak.added_date, 
                    d.filename, d.document_type, d.page_count
                FROM AgentKnowledge ak
                JOIN Documents d ON ak.document_id = d.document_id
                WHERE ak.agent_id = ? AND ak.is_active = 1
                    AND ak.added_by = ?
                ORDER BY ak.added_date DESC
            """, agent_id, str(current_user.id))

            for row in cursor.fetchall():
                knowledge_items.append({
                    'knowledge_id': row[0],
                    'agent_id': row[1],
                    'document_id': row[2],
                    'description': row[3],
                    'added_date': row[4].strftime('%Y-%m-%d %H:%M:%S') if row[4] else None,
                    'filename': row[5],
                    'document_type': row[6],
                    'page_count': row[7]
                })
        except Exception as e:
            print(f"Error getting user specific agent knowledge: {str(e)}")
            logging.error(f"Error getting user specific agent knowledge: {str(e)}")
        
        cursor.close()
        conn.close()
        
        return knowledge_items
    except Exception as e:
        logging.error(f"Error getting user specific agent knowledge: {str(e)}")
        return []
    
# def get_agent_knowledge(agent_id):
#     """Get knowledge items associated with an agent"""
#     try:
#         conn = get_db_connection()
#         cursor = conn.cursor()
        
#         # Set tenant context
#         cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
#         # Get knowledge items
#         cursor.execute("""
#             SELECT ak.knowledge_id, ak.agent_id, ak.document_id, ak.description, ak.added_date, 
#                    d.filename, d.document_type, d.page_count
#             FROM AgentKnowledge ak
#             JOIN Documents d ON ak.document_id = d.document_id
#             WHERE ak.agent_id = ? AND ak.is_active = 1
#             ORDER BY ak.added_date DESC
#         """, agent_id)
        
#         # Format results
#         knowledge_items = []
#         for row in cursor.fetchall():
#             knowledge_items.append({
#                 'knowledge_id': row[0],
#                 'agent_id': row[1],
#                 'document_id': row[2],
#                 'description': row[3],
#                 'added_date': row[4].strftime('%Y-%m-%d %H:%M:%S') if row[4] else None,
#                 'filename': row[5],
#                 'document_type': row[6],
#                 'page_count': row[7]
#             })
        
#         cursor.close()
#         conn.close()
        
#         return knowledge_items
#     except Exception as e:
#         logging.error(f"Error getting agent knowledge: {str(e)}")
#         return []

# Function to add a knowledge item to an agent
def add_agent_knowledge(agent_id, document_id, description='', user_id=None):
    """Add a knowledge item to an agent"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Insert knowledge item
        if not user_id:
            cursor.execute("""
                INSERT INTO AgentKnowledge (agent_id, document_id, description, added_date, is_active)
                VALUES (?, ?, ?, getutcdate(), 1)
            """, agent_id, document_id, description)
        else:
            cursor.execute("""
                INSERT INTO AgentKnowledge (agent_id, document_id, description, added_date, is_active, added_by)
                VALUES (?, ?, ?, getutcdate(), 1, ?)
            """, agent_id, document_id, description, str(user_id))

        # Get the new knowledge_id
        cursor.execute("SELECT @@IDENTITY")
        knowledge_id = cursor.fetchone()[0]

        # Flag as knowledge document (required due to standard document processing API - look to change this to standard knowledge processing API instead)
        # TODO: This will not be required if the standard knowledge API is used. I attempted but it was proving to be more work and not worth the time.
        cursor.execute("""UPDATE Documents SET is_knowledge_document = 1 WHERE document_id = ?""", document_id)
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return knowledge_id
    except Exception as e:
        logging.error(f"Error adding agent knowledge: {str(e)}")
        return None

# Function to update a knowledge item
def update_agent_knowledge(knowledge_id, description):
    """Update a knowledge item's description"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Update knowledge item
        cursor.execute("""
            UPDATE AgentKnowledge
            SET description = ?
            WHERE knowledge_id = ?
        """, description, knowledge_id)
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return True
    except Exception as e:
        logging.error(f"Error updating agent knowledge: {str(e)}")
        return False

# Function to delete a knowledge item
def delete_agent_knowledge(knowledge_id):
    """Delete a knowledge item (soft delete) and clean up persistent Excel files"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        # Check if this is an Excel file with a persistent copy before soft-deleting
        try:
            cursor.execute("""
                SELECT d.original_path, d.filename
                FROM AgentKnowledge ak
                JOIN Documents d ON ak.document_id = d.document_id
                WHERE ak.knowledge_id = ?
            """, knowledge_id)
            doc_row = cursor.fetchone()
            if doc_row:
                original_path = doc_row[0]
                filename = doc_row[1] or ''
                is_excel = filename.lower().endswith(('.xlsx', '.xls'))
                if is_excel and original_path:
                    import config as cfg
                    knowledge_files_dir = cfg.EXCEL_KNOWLEDGE_FILES_DIR
                    # Only delete if the path is within our knowledge_files directory
                    if knowledge_files_dir in str(original_path):
                        import shutil
                        persist_dir = os.path.dirname(original_path)
                        if os.path.isdir(persist_dir):
                            shutil.rmtree(persist_dir)
                            print(f'Cleaned up persistent Excel directory: {persist_dir}')
        except Exception as cleanup_err:
            logging.warning(f"Error during Excel file cleanup: {cleanup_err}")

        # Soft delete
        cursor.execute("""
            UPDATE AgentKnowledge
            SET is_active = 0
            WHERE knowledge_id = ?
        """, knowledge_id)

        conn.commit()
        cursor.close()
        conn.close()

        return True
    except Exception as e:
        logging.error(f"Error deleting agent knowledge: {str(e)}")
        return False

# Function to process a document and add it as knowledge using the API
def process_document_as_knowledge(file_path, agent_id, description=''):
    """
    Process a document and add it as knowledge for an agent using the document API
    
    Args:
        file_path: Path to the document file
        agent_id: Agent ID to associate the document with
        description: Optional description of the knowledge
        
    Returns:
        Dictionary with processing result
    """
    try:
        # Get document API base URL
        doc_api_url = get_document_api_base_url()
        
        # Create the API endpoint URL
        process_url = f"{doc_api_url}/document/process"
        
        # Prepare the form data
        form_data = {
            'filePath': file_path,
            'force_ai_extraction': 'true'
        }

        # Configurable timeout for large documents — the document API
        # splits PDFs into 100-page chunks internally, so a 400+ page
        # PDF may need 20+ minutes of processing time
        import config as cfg
        timeout_seconds = cfg.DOC_PROCESSING_TIMEOUT_MINUTES * 60
        response = requests.post(process_url, data=form_data, timeout=timeout_seconds)

        # Check if the request was successful
        if response.status_code == 200:
            result = response.json()
            
            # Add as knowledge if document was processed successfully
            if result['status'] == 'success' and 'document_id' in result:
                knowledge_id = add_agent_knowledge(
                    agent_id=agent_id,
                    document_id=result['document_id'],
                    description=description
                )
                
                if knowledge_id:
                    return {
                        "status": "success",
                        "message": f"Document processed and added as knowledge",
                        "knowledge_id": knowledge_id,
                        "document_id": result['document_id'],
                        "document_type": result.get('document_type', 'unknown'),
                        "page_count": result.get('page_count', 0)
                    }
                else:
                    return {
                        "status": "error",
                        "message": "Failed to add document as knowledge"
                    }
            else:
                return {
                    "status": "error",
                    "message": result.get('message', 'Failed to process document')
                }
        else:
            return {
                "status": "error",
                "message": f"API request failed with status code {response.status_code}"
            }
            
    except requests.exceptions.Timeout:
        logging.error(f"Document processing timed out for: {file_path}")
        return {
            "status": "error",
            "message": "Document processing timed out. Large documents (400+ pages) may require extended processing time. Please try uploading again or splitting the document into smaller parts."
        }
    except requests.exceptions.ConnectionError as e:
        logging.error(f"Connection error processing document: {str(e)}")
        return {
            "status": "error",
            "message": "Could not connect to the document processing service. Please ensure the service is running and try again."
        }
    except Exception as e:
        logging.error(f"Error processing document as knowledge: {str(e)}")
        return {
            "status": "error",
            "message": f"Error: {str(e)}"
        }

# Routes to add to app.py
def register_knowledge_routes(app):
    # Route to get agent knowledge
    @app.route('/get/agent_knowledge/<int:agent_id>', methods=['GET'])
    @cross_origin()
    @login_required
    def get_agent_knowledge_route(agent_id):
        """Get knowledge items for an agent"""
        knowledge_items = get_agent_knowledge(agent_id)
        return jsonify(knowledge_items)
    
    # Route to add knowledge to an agent
    @app.route('/add/agent_knowledge', methods=['POST'])
    @cross_origin()
    @login_required
    def add_agent_knowledge_route():
        """Add knowledge to an agent"""
        try:
            print(f'Adding agent knowledge...')
            # Get form data
            agent_id = request.form.get('agent_id')
            description = request.form.get('description', '')

            print(f'Agent ID: {agent_id}')
            print(f'Description: {description}')
            
            # Check for file
            if 'file' not in request.files:
                print('No file part')
                return jsonify({
                    "status": "error",
                    "message": "No file part"
                }), 400
                
            file = request.files['file']
            if file.filename == '':
                print('No selected file')
                return jsonify({
                    "status": "error",
                    "message": "No selected file"
                }), 400
            
            print('Uploading file...')
            print('Upload folder:', str(app.config['UPLOAD_FOLDER']))
            # Save file temporarily
            filename = str(uuid.uuid4()) + '_' + file.filename
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            print('File uploaded successfully', str(file_path))
            
            # Process document and add as knowledge
            result = process_document_as_knowledge(
                file_path=file_path,
                agent_id=agent_id,
                description=description
            )
            
            # Clean up temporary file
            try:
                # Reload agents
                global active_agents
                active_agents = load_agents()

                # For Excel files: persist original and generate metadata before deleting temp
                original_filename = file.filename
                is_excel = original_filename.lower().endswith(('.xlsx', '.xls'))

                if is_excel and result.get('status') == 'success' and result.get('document_id'):
                    import shutil
                    import config as cfg
                    from agent_excel_tools import generate_excel_metadata

                    doc_id = result['document_id']
                    persist_dir = os.path.join(
                        os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__))),
                        cfg.EXCEL_KNOWLEDGE_FILES_DIR, doc_id
                    )
                    os.makedirs(persist_dir, exist_ok=True)
                    persistent_path = os.path.join(persist_dir, original_filename)
                    shutil.copy2(file_path, persistent_path)
                    print(f'Excel file persisted to: {persistent_path}')

                    # Update Documents.original_path to the persistent location
                    try:
                        conn_persist = get_db_connection()
                        cursor_persist = conn_persist.cursor()
                        cursor_persist.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
                        cursor_persist.execute(
                            "UPDATE Documents SET original_path = ? WHERE document_id = ?",
                            persistent_path, doc_id
                        )
                        conn_persist.commit()
                        cursor_persist.close()
                        conn_persist.close()
                    except Exception as path_err:
                        logging.warning(f"Failed to update persistent path: {path_err}")

                    # Generate and store metadata profile
                    try:
                        metadata = generate_excel_metadata(persistent_path)
                        conn_meta = get_db_connection()
                        cursor_meta = conn_meta.cursor()
                        cursor_meta.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
                        cursor_meta.execute(
                            "UPDATE Documents SET document_metadata = ? WHERE document_id = ?",
                            json.dumps(metadata, default=str), doc_id
                        )
                        conn_meta.commit()
                        cursor_meta.close()
                        conn_meta.close()
                        print(f'Excel metadata stored ({metadata.get("total_rows", 0)} total rows)')
                    except Exception as meta_err:
                        logging.warning(f"Failed to generate Excel metadata: {meta_err}")

                os.remove(file_path)
            except:
                pass
                
            return jsonify(result)
            
        except Exception as e:
            print(f"Error adding agent knowledge: {str(e)}")
            logging.error(f"Error adding agent knowledge: {str(e)}")
            return jsonify({
                "status": "error",
                "message": f"Error: {str(e)}"
            }), 500
    
    # Route to update knowledge
    @app.route('/update/agent_knowledge/<int:knowledge_id>', methods=['POST'])
    @cross_origin()
    @login_required
    def update_agent_knowledge_route(knowledge_id):
        """Update knowledge description"""
        try:
            data = request.json
            description = data.get('description', '')
            
            result = update_agent_knowledge(knowledge_id, description)
            
            if result:
                return jsonify({
                    "status": "success",
                    "message": "Knowledge updated successfully"
                })
            else:
                return jsonify({
                    "status": "error",
                    "message": "Failed to update knowledge"
                }), 500
                
        except Exception as e:
            logging.error(f"Error updating agent knowledge: {str(e)}")
            return jsonify({
                "status": "error",
                "message": f"Error: {str(e)}"
            }), 500
    
    # Route to delete knowledge
    @app.route('/delete/agent_knowledge/<int:knowledge_id>', methods=['POST'])
    @cross_origin()
    @login_required
    def delete_agent_knowledge_route(knowledge_id):
        """Delete knowledge"""
        try:
            result = delete_agent_knowledge(knowledge_id)
            
            if result:
                return jsonify({
                    "status": "success",
                    "message": "Knowledge deleted successfully"
                })
            else:
                return jsonify({
                    "status": "error",
                    "message": "Failed to delete knowledge"
                }), 500
                
        except Exception as e:
            logging.error(f"Error deleting agent knowledge: {str(e)}")
            return jsonify({
                "status": "error",
                "message": f"Error: {str(e)}"
            }), 500

    # Route for knowledge management UI
    @app.route('/agent_knowledge/<int:agent_id>')
    @login_required
    def agent_knowledge_page(agent_id):
        """Render agent knowledge management page"""
        return render_template('agent_knowledge.html', agent_id=agent_id)
    

    
