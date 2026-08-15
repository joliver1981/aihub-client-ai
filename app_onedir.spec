# -*- mode: python ; coding: utf-8 -*-
# ONEDIR VERSION - Creates dist/app/ folder instead of single exe
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
print("Validating source data paths...")
print("="*60)
validate_data_path('templates', 'Main templates folder')
validate_data_path('static', 'Static files folder')
validate_data_path('assistant_docs', 'Assistant docs folder')
validate_data_path('agent_environments', 'Agent environments module')
validate_data_path('agent_environments/templates', 'Environment templates')
validate_data_path('agent_environments/static', 'Environment static files')
print("="*60 + "\n")

# =============================================================================
# Collect Package Dependencies
# =============================================================================
# collect_all() returns (datas, binaries, hiddenimports) — order matters!

# Packages with heavy dynamic imports that PyInstaller misses
packages_to_collect = [
    'pandasai',
    'sqlglot',   # NLQ V3 sql_gate.py — dynamic dialect submodules; direct import, don't rely on pandasai transitivity
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

# ---------------------------------------------------------------------------
# Collect metadata for ALL installed packages (catch-all)
# Many packages use importlib.metadata.version() at runtime which fails in
# PyInstaller if the dist-info is missing. Instead of adding them one-by-one,
# we collect every installed package's metadata upfront.
# ---------------------------------------------------------------------------
import importlib.metadata as importlib_metadata

extra_datas = []
for dist in importlib_metadata.distributions():
    pkg_name = dist.metadata["Name"]
    try:
        extra_datas.extend(copy_metadata(pkg_name))
    except Exception:
        pass  # some packages have no copyable metadata — that's fine

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

# Compliance YAML taxonomies loaded at runtime by compliance_engine.py
# (retailer_compliance.yaml is loaded unconditionally during ComplianceEngine.__init__
# and the app won't start without it; customer_vendor_requirements.yaml is shipped
# alongside as source-of-truth reference for the corresponding DB-stored schema).
if os.path.exists('schemas'):
    app_datas.append(('schemas', 'schemas'))

# Routes loaded dynamically via spec_from_file_location (need raw .py files)
if os.path.exists('routes/data_explorer.py'):
    app_datas.append(('routes/data_explorer.py', 'routes'))

# Assistant docs - include entire folder with all markdown files
if os.path.exists('assistant_docs'):
    app_datas.append(('assistant_docs', 'assistant_docs'))
    # Also explicitly add any .md files to ensure they're included
    for md_file in glob.glob('assistant_docs/**/*.md', recursive=True):
        rel_path = os.path.dirname(md_file)
        if (md_file, rel_path) not in app_datas:
            app_datas.append((md_file, rel_path))

# Agent environments module - include the entire package
if os.path.exists('agent_environments'):
    # Include __init__.py and all Python files (needed for os.path.exists check at runtime)
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
    
    # Include python-bundle if it exists (for custom environments feature)
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
    ['wsgi.py'],
    pathex=[],
    binaries=all_collected_binaries,
    datas=app_datas,
    hiddenimports=[
        'openpyxl',
        'PyMuPDF',
        'docx',
        'onnxruntime',
        # LDAP / Enterprise Identity
        'ldap3',
        'ldap3.core',
        'ldap3.core.exceptions',
        'ldap3.utils',
        'ldap3.utils.log',
        'pyasn1',
        'auth',
        'auth.base_provider',
        'auth.local_provider',
        'auth.ldap_provider',
        'auth.user_provisioner',
        'auth.provider_chain',
        'auth_identity_routes',
        # Agent environments module imports
        'agent_environments',
        'agent_environments.environment_api',
        'agent_environments.environment_manager',
        'agent_environments.environment_config',
        'agent_environments.cloud_config_manager',
        # Command Center / cross-service JWT auth. shared_auth does a lazy
        # `import jwt` inside functions, which PyInstaller's static analysis
        # misses — list both explicitly so the frozen build can sign/verify.
        'shared_auth',
        'jwt',
        # SFTP for the workflow File Transfer node + CC transfer tools.
        # sftp_transfer does a lazy `import paramiko` inside _import_paramiko()
        # — same trap as jwt above; list it and its binary deps explicitly so
        # the frozen client build can speak SFTP (FTP/FTPS are stdlib).
        'paramiko',
        'cryptography',
        'bcrypt',
        'nacl',
        # Document search/records: app.py imports these lazily INSIDE the route
        # bodies (/api/internal/document-search-unified, /document-records), which
        # PyInstaller's static analysis misses — the frozen client would return
        # "Document search failed" on both agent surfaces with no build-time
        # signal. Same trap as jwt/paramiko above.
        'document_search_wrapper',
        'document_records_query',
        'doc_search_v2',
        'doc_search_v2.factory',
        'doc_search_v2.needle',
        'doc_search_v2.sweep',
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
    name='app',
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

# ONEDIR: COLLECT gathers everything into dist/app/
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='app',
)
