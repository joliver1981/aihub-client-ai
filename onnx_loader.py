# onnx_loader.py
import sys
import os
import importlib.util
import importlib.machinery

def setup_external_onnx():
    """Setup paths to use external ONNX runtime"""
    # Get the directory where the exe is located
    if getattr(sys, 'frozen', False):
        # Running as compiled exe
        app_dir = os.path.dirname(sys.executable)
    else:
        # Running as script
        app_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Path to external ONNX
    onnx_dir = os.path.join(app_dir, 'onnxruntime')
    
    if os.path.exists(onnx_dir):
        # Add all ONNX paths to sys.path FIRST
        sys.path.insert(0, os.path.join(onnx_dir, 'capi'))
        sys.path.insert(0, onnx_dir)
        sys.path.insert(0, app_dir)
        
        # Add to Windows DLL search paths
        if hasattr(os, 'add_dll_directory'):
            os.add_dll_directory(app_dir)
            os.add_dll_directory(onnx_dir)
            os.add_dll_directory(os.path.join(onnx_dir, 'capi'))
        
        # Update PATH environment variable
        os.environ['PATH'] = f"{app_dir};{onnx_dir};{os.path.join(onnx_dir, 'capi')};{os.environ.get('PATH', '')}"
        
        print(f"External ONNX Runtime configured from: {onnx_dir}")
        
        # Try method 1: Direct import
        try:
            # Remove any existing onnxruntime from sys.modules
            if 'onnxruntime' in sys.modules:
                print('Deleting existing onnxruntime module...')
                del sys.modules['onnxruntime']
            
            # Import using the standard mechanism
            import onnxruntime
            print(f"ONNX Runtime loaded successfully")
            return True
        except Exception as e:
            print(f"Standard import failed: {e}")
        
        # Try method 2: Manual module loading
        try:
            # Load __init__.py manually
            init_path = os.path.join(onnx_dir, '__init__.py')
            if os.path.exists(init_path):
                spec = importlib.util.spec_from_file_location('onnxruntime', init_path)
                if spec and spec.loader:
                    onnxruntime = importlib.util.module_from_spec(spec)
                    sys.modules['onnxruntime'] = onnxruntime
                    spec.loader.exec_module(onnxruntime)
                    print("ONNX Runtime loaded via manual import")
                    return True
        except Exception as e:
            print(f"Manual module loading failed: {e}")
        
        print("Warning: ONNX Runtime could not be loaded")
        return False
    else:
        print(f"Warning: ONNX Runtime not found at: {onnx_dir}")
        return False

# Call this immediately
onnx_loaded = setup_external_onnx()

# If ONNX didn't load, we can still continue - ChromaDB will use a different embedding function
if not onnx_loaded:
    print("Application will continue without ONNX Runtime support")