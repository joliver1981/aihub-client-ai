# -*- mode: python ; coding: utf-8 -*-
# ONEDIR VERSION - Creates dist/wsgi_executor_service/ folder instead of single exe
from PyInstaller.utils.hooks import copy_metadata, collect_all
import os
import glob

block_cipher = None

# =============================================================================
# Path Validation - Warn if source folders are missing
# =============================================================================
def validate_data_path(src_path, description):
    """Validate source path exists and has content"""
    if not os.path.exists(src_path):
        print(f"⚠️  WARNING: {description} not found at '{src_path}'")
        return False
    if os.path.isdir(src_path):
        contents = os.listdir(src_path)
        if not contents:
            print(f"⚠️  WARNING: {description} exists but is empty at '{src_path}'")
            return False
        print(f"✅ Found {description}: {len(contents)} items in '{src_path}'")
    return True

# Validate critical paths before build
print("\n" + "="*60)
print("Validating source data paths for executor service...")
print("="*60)
validate_data_path('templates', 'Main templates folder')
validate_data_path('static', 'Static files folder')
validate_data_path('agent_environments', 'Agent environments module')
print("="*60 + "\n")

# =============================================================================
# Collect Package Dependencies
# =============================================================================
# collect_all() returns (datas, binaries, hiddenimports) — order matters!

packages_to_collect = [
    'pandasai',
    'duckdb',
    'chromadb',
    'tokenizers',
    'transformers',
    'sentence_transformers',
    'certifi',
    'openai',
    'pydantic',
    'pydantic_core',
    'langchain',
    'langchain_core',
    'langchain_openai',
    'langchain_community',
    'langchain_text_splitters',
    'langsmith',
]

all_collected_datas = []
all_collected_binaries = []
all_collected_hiddenimports = []

for pkg in packages_to_collect:
    try:
        datas, binaries, hiddenimports = collect_all(pkg)
        all_collected_datas.extend(datas)
        all_collected_binaries.extend(binaries)
        all_collected_hiddenimports.extend(hiddenimports)
        print(f"✅ collect_all('{pkg}'): {len(datas)} datas, {len(binaries)} binaries, {len(hiddenimports)} hiddenimports")
    except Exception as e:
        print(f"⚠️  collect_all('{pkg}') failed: {e}")

# Filter out invalid hidden imports caused by backup files (e.g. 'openai._base_client - rollback')
all_collected_hiddenimports = [h for h in all_collected_hiddenimports if ' ' not in h and '-' not in h.split('.')[-1]]

# Collect metadata for ALL installed packages (catch-all to prevent PackageNotFoundError)
import importlib.metadata as importlib_metadata

extra_datas = []
for dist in importlib_metadata.distributions():
    pkg_name = dist.metadata["Name"]
    try:
        extra_datas.extend(copy_metadata(pkg_name))
    except Exception:
        pass

print(f"✅ Collected metadata for {len(extra_datas)} distribution entries")

# =============================================================================
# Build Data Files List - With Existence Checks
# =============================================================================
app_datas = []

# Main templates and static
if os.path.exists('templates'):
    app_datas.append(('templates', 'templates'))
if os.path.exists('static'):
    app_datas.append(('static', 'static'))

# Compliance YAML taxonomies — workflow_execution imports compliance_engine when
# a workflow contains a Compliance Process or Compliance Excel Export node, and
# compliance_engine loads schemas/retailer_compliance.yaml at instance init.
# Without this the executor service crashes the moment a compliance node fires.
if os.path.exists('schemas'):
    app_datas.append(('schemas', 'schemas'))

# Agent environments module - include the entire package
if os.path.exists('agent_environments'):
    # Include __init__.py (needed for os.path.exists check at runtime)
    if os.path.exists('agent_environments/__init__.py'):
        app_datas.append(('agent_environments/__init__.py', 'agent_environments'))
    
    # Include templates subfolder
    if os.path.exists('agent_environments/templates'):
        app_datas.append(('agent_environments/templates', 'agent_environments/templates'))
    
    # Include static subfolder
    if os.path.exists('agent_environments/static'):
        app_datas.append(('agent_environments/static', 'agent_environments/static'))
    
    # Include docs subfolder
    if os.path.exists('agent_environments/docs'):
        app_datas.append(('agent_environments/docs', 'agent_environments/docs'))
    
    # Include python-bundle if it exists
    if os.path.exists('agent_environments/python-bundle'):
        app_datas.append(('agent_environments/python-bundle', 'agent_environments/python-bundle'))
    
    # Include python-bundle-requirements if it exists
    if os.path.exists('agent_environments/python-bundle-requirements'):
        app_datas.append(('agent_environments/python-bundle-requirements', 'agent_environments/python-bundle-requirements'))

# Add all collected package datas
app_datas.extend(all_collected_datas)
app_datas.extend(extra_datas)

# =============================================================================
# Analysis
# =============================================================================
a = Analysis(
    ['wsgi_executor_service.py'],
    pathex=[],
    binaries=all_collected_binaries,
    datas=app_datas,
    hiddenimports=[
        'openpyxl',
        'onnxruntime',
        # Agent environments module imports
        'agent_environments',
        'agent_environments.environment_api',
        'agent_environments.environment_manager',
        'agent_environments.environment_config',
        'agent_environments.cloud_config_manager',
        # SFTP for the workflow File Transfer node — the executor service runs
        # workflows too, so it needs paramiko exactly like the main app
        # (lazy import inside sftp_transfer._import_paramiko; see app_onedir.spec).
        'paramiko',
        'cryptography',
        'bcrypt',
        'nacl',
    ] + all_collected_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['user_config.py', 'user_prompts.py'],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ONEDIR: EXE excludes binaries - they go in COLLECT
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='wsgi_executor_service',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ONEDIR: COLLECT gathers everything into dist/wsgi_executor_service/
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='wsgi_executor_service',
)
