# custom_tool_import_routes.py
# Routes for importing custom tools independently of agents

from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required
import os
import json
import shutil
import tempfile
import zipfile
import logging
from datetime import datetime
from werkzeug.utils import secure_filename

# Create the blueprint
custom_tool_import_bp = Blueprint('custom_tool_import', __name__)

# Configure allowed extensions
ALLOWED_EXTENSIONS = {'zip'}

def allowed_file(filename):
    """Check if file has an allowed extension"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@custom_tool_import_bp.route('/api/tool/analyze_package', methods=['POST'])
@login_required
def analyze_tool_package():
    """Analyze a custom tool package before import"""
    try:
        if 'file' not in request.files:
            return jsonify({"status": "error", "message": "No file provided"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"status": "error", "message": "No file selected"}), 400
        
        if not allowed_file(file.filename):
            return jsonify({"status": "error", "message": "Invalid file type. Only ZIP files are allowed"}), 400
        
        # Create temporary directory
        temp_dir = tempfile.mkdtemp()
        analysis_results = {
            "status": "success",
            "package_info": {},
            "tools": [],
            "conflicts": {
                "existing_tools": []
            }
        }
        
        try:
            # Save and extract zip file
            zip_path = os.path.join(temp_dir, secure_filename(file.filename))
            file.save(zip_path)
            
            extract_dir = os.path.join(temp_dir, 'extracted')
            os.makedirs(extract_dir)
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
            
            # Analyze package structure
            tools_found = []
            
            # Check if it's a single tool or multiple tools
            # Single tool structure: config.json and code.py in root
            # Multiple tools structure: folders with config.json and code.py
            
            root_files = os.listdir(extract_dir)
            
            # Check for single tool package
            if 'config.json' in root_files and 'code.py' in root_files:
                # Single tool package
                config_path = os.path.join(extract_dir, 'config.json')
                with open(config_path, 'r') as f:
                    config = json.load(f)
                
                tool_name = config.get('function_name', 'unnamed_tool')
                tools_found.append({
                    "name": tool_name,
                    "description": config.get('description', ''),
                    "parameters": config.get('parameters', []),
                    "parameter_types": config.get('parameter_types', []),
                    "output_type": config.get('output_type', 'str'),
                    "type": "single",
                    "path": extract_dir
                })
                
                analysis_results["package_info"]["type"] = "single_tool"
                analysis_results["package_info"]["name"] = tool_name
                
            else:
                # Check for multiple tools package
                tools_count = 0
                for item in root_files:
                    item_path = os.path.join(extract_dir, item)
                    if os.path.isdir(item_path):
                        config_path = os.path.join(item_path, 'config.json')
                        code_path = os.path.join(item_path, 'code.py')
                        
                        if os.path.exists(config_path) and os.path.exists(code_path):
                            with open(config_path, 'r') as f:
                                config = json.load(f)
                            
                            tool_name = item  # Use folder name as tool name
                            tools_found.append({
                                "name": tool_name,
                                "description": config.get('description', ''),
                                "parameters": config.get('parameters', []),
                                "parameter_types": config.get('parameter_types', []),
                                "output_type": config.get('output_type', 'str'),
                                "type": "folder",
                                "path": item_path
                            })
                            tools_count += 1
                
                if tools_count > 0:
                    analysis_results["package_info"]["type"] = "multiple_tools"
                    analysis_results["package_info"]["count"] = tools_count
                else:
                    return jsonify({
                        "status": "error", 
                        "message": "No valid custom tools found in the package"
                    }), 400
            
            analysis_results["tools"] = tools_found
            
            # Check for conflicts with existing tools
            import config as cfg
            existing_tools = []
            if os.path.exists(cfg.CUSTOM_TOOLS_FOLDER):
                existing_tools = [d for d in os.listdir(cfg.CUSTOM_TOOLS_FOLDER) 
                                if os.path.isdir(os.path.join(cfg.CUSTOM_TOOLS_FOLDER, d))]
            
            for tool in tools_found:
                if tool["name"] in existing_tools:
                    analysis_results["conflicts"]["existing_tools"].append(tool["name"])
            
            # Store temp_dir in session for actual import
            analysis_results["temp_dir"] = temp_dir
            analysis_results["package_info"]["filename"] = file.filename
            analysis_results["package_info"]["timestamp"] = datetime.now().isoformat()
            
            return jsonify(analysis_results)
            
        except Exception as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise e
            
    except Exception as e:
        logging.error(f"Error analyzing tool package: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@custom_tool_import_bp.route('/api/tool/import', methods=['POST'])
@login_required
def import_custom_tools():
    """Import custom tools from a package"""
    try:
        if 'file' not in request.files:
            return jsonify({"status": "error", "message": "No file provided"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"status": "error", "message": "No file selected"}), 400
        
        if not allowed_file(file.filename):
            return jsonify({"status": "error", "message": "Invalid file type. Only ZIP files are allowed"}), 400
        
        # Get import options from request
        import_options = request.form.to_dict()
        overwrite_existing = import_options.get('overwrite_existing', 'false').lower() == 'true'
        rename_conflicts = import_options.get('rename_conflicts', 'true').lower() == 'true'
        
        # Create temporary directory
        temp_dir = tempfile.mkdtemp()
        import_results = {
            "status": "success",
            "imported": [],
            "skipped": [],
            "failed": [],
            "renamed": []
        }
        
        try:
            # Save and extract zip file
            zip_path = os.path.join(temp_dir, secure_filename(file.filename))
            file.save(zip_path)
            
            extract_dir = os.path.join(temp_dir, 'extracted')
            os.makedirs(extract_dir)
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
            
            import config as cfg
            
            # Process tools based on package structure
            root_files = os.listdir(extract_dir)
            
            # Single tool package
            if 'config.json' in root_files and 'code.py' in root_files:
                config_path = os.path.join(extract_dir, 'config.json')
                with open(config_path, 'r') as f:
                    config = json.load(f)
                
                tool_name = config.get('function_name', 'unnamed_tool')
                result = import_single_tool(
                    tool_name, extract_dir, cfg.CUSTOM_TOOLS_FOLDER,
                    overwrite_existing, rename_conflicts
                )
                
                if result['status'] == 'imported':
                    import_results['imported'].append(result)
                elif result['status'] == 'skipped':
                    import_results['skipped'].append(result)
                elif result['status'] == 'renamed':
                    import_results['renamed'].append(result)
                else:
                    import_results['failed'].append(result)
            
            # Multiple tools package
            else:
                for item in root_files:
                    item_path = os.path.join(extract_dir, item)
                    if os.path.isdir(item_path):
                        config_path = os.path.join(item_path, 'config.json')
                        code_path = os.path.join(item_path, 'code.py')
                        
                        if os.path.exists(config_path) and os.path.exists(code_path):
                            tool_name = item
                            result = import_single_tool(
                                tool_name, item_path, cfg.CUSTOM_TOOLS_FOLDER,
                                overwrite_existing, rename_conflicts
                            )
                            
                            if result['status'] == 'imported':
                                import_results['imported'].append(result)
                            elif result['status'] == 'skipped':
                                import_results['skipped'].append(result)
                            elif result['status'] == 'renamed':
                                import_results['renamed'].append(result)
                            else:
                                import_results['failed'].append(result)
            
            # Add summary
            import_results['summary'] = {
                'total_processed': len(import_results['imported']) + len(import_results['skipped']) + 
                                 len(import_results['failed']) + len(import_results['renamed']),
                'imported': len(import_results['imported']),
                'skipped': len(import_results['skipped']),
                'failed': len(import_results['failed']),
                'renamed': len(import_results['renamed'])
            }
            
            return jsonify(import_results)
            
        finally:
            # Cleanup temporary directory
            shutil.rmtree(temp_dir, ignore_errors=True)
            
    except Exception as e:
        logging.error(f"Error importing custom tools: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

def import_single_tool(tool_name, source_path, dest_folder, overwrite, rename_on_conflict):
    """Import a single custom tool"""
    try:
        dest_path = os.path.join(dest_folder, tool_name)
        
        # Check if tool already exists
        if os.path.exists(dest_path):
            if overwrite:
                # Remove existing and copy new
                shutil.rmtree(dest_path)
                shutil.copytree(source_path, dest_path)
                return {
                    'status': 'imported',
                    'name': tool_name,
                    'message': f'Tool {tool_name} imported (overwritten existing)'
                }
            elif rename_on_conflict:
                # Generate new name with timestamp
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                new_name = f"{tool_name}_{timestamp}"
                new_dest_path = os.path.join(dest_folder, new_name)
                shutil.copytree(source_path, new_dest_path)
                
                # Update config.json with new name
                config_path = os.path.join(new_dest_path, 'config.json')
                if os.path.exists(config_path):
                    with open(config_path, 'r') as f:
                        config = json.load(f)
                    config['function_name'] = new_name
                    with open(config_path, 'w') as f:
                        json.dump(config, f, indent=4)
                
                return {
                    'status': 'renamed',
                    'original_name': tool_name,
                    'name': new_name,
                    'message': f'Tool imported as {new_name} (name conflict resolved)'
                }
            else:
                return {
                    'status': 'skipped',
                    'name': tool_name,
                    'message': f'Tool {tool_name} skipped (already exists)'
                }
        else:
            # Copy tool to destination
            shutil.copytree(source_path, dest_path)
            return {
                'status': 'imported',
                'name': tool_name,
                'message': f'Tool {tool_name} imported successfully'
            }
            
    except Exception as e:
        return {
            'status': 'failed',
            'name': tool_name,
            'message': f'Failed to import {tool_name}: {str(e)}'
        }

@custom_tool_import_bp.route('/api/tool/export/<tool_name>', methods=['GET'])
@login_required
def export_single_tool(tool_name):
    """Export a single custom tool as a zip file"""
    try:
        import config as cfg
        
        tool_path = os.path.join(cfg.CUSTOM_TOOLS_FOLDER, tool_name)
        if not os.path.exists(tool_path):
            return jsonify({"status": "error", "message": "Tool not found"}), 404
        
        # Create temporary zip file
        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, f"{tool_name}.zip")
        
        # Create zip archive
        shutil.make_archive(
            base_name=os.path.join(temp_dir, tool_name),
            format='zip',
            root_dir=tool_path
        )
        
        # Send file
        from flask import send_file
        return send_file(
            zip_path,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f"{tool_name}.zip"
        )
        
    except Exception as e:
        logging.error(f"Error exporting tool {tool_name}: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@custom_tool_import_bp.route('/api/tool/export_multiple', methods=['POST'])
@login_required
def export_multiple_tools():
    """Export multiple custom tools as a single zip file"""
    try:
        import config as cfg
        
        data = request.get_json()
        tool_names = data.get('tools', [])
        
        if not tool_names:
            return jsonify({"status": "error", "message": "No tools specified"}), 400
        
        # Create temporary directory for packaging
        temp_dir = tempfile.mkdtemp()
        package_dir = os.path.join(temp_dir, 'custom_tools_package')
        os.makedirs(package_dir)
        
        exported_tools = []
        failed_tools = []
        
        for tool_name in tool_names:
            tool_path = os.path.join(cfg.CUSTOM_TOOLS_FOLDER, tool_name)
            if os.path.exists(tool_path):
                dest_path = os.path.join(package_dir, tool_name)
                shutil.copytree(tool_path, dest_path)
                exported_tools.append(tool_name)
            else:
                failed_tools.append(tool_name)
        
        if not exported_tools:
            return jsonify({
                "status": "error", 
                "message": "No valid tools found to export"
            }), 400
        
        # Create zip archive
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        zip_name = f"custom_tools_{timestamp}"
        zip_path = os.path.join(temp_dir, f"{zip_name}.zip")
        
        shutil.make_archive(
            base_name=os.path.join(temp_dir, zip_name),
            format='zip',
            root_dir=package_dir
        )
        
        # Send file
        from flask import send_file
        return send_file(
            zip_path,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f"{zip_name}.zip"
        )
        
    except Exception as e:
        logging.error(f"Error exporting multiple tools: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500
