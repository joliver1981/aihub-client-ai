# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import copy_metadata, collect_all
import os
import glob

block_cipher = None


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


print("\n============================================================")
print("Validating source data paths...")
print("============================================================")
validate_data_path('templates', 'Main templates folder')
validate_data_path('static', 'Static files folder')
validate_data_path('assistant_docs', 'Assistant docs folder')
validate_data_path('agent_environments', 'Agent environments module')
validate_data_path('agent_environments/templates', 'Environment templates')
validate_data_path('agent_environments/static', 'Environment static files')
print("============================================================\n")

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

all_collected_hiddenimports = [h for h in all_collected_hiddenimports if ' ' not in h and '-' not in h.split('.')[-1]]

import importlib.metadata as importlib_metadata

extra_datas = []
for dist in importlib_metadata.distributions():
    pkg_name = dist.metadata['Name']
    try:
        extra_datas.extend(copy_metadata(pkg_name))
    except Exception:
        pass
print(f"✅ Collected metadata for {len(extra_datas)} distribution entries")


app_datas = []

if os.path.exists('templates'):
    app_datas.append(('templates', 'templates'))
if os.path.exists('static'):
    app_datas.append(('static', 'static'))

if os.path.exists('schemas'):
    app_datas.append(('schemas', 'schemas'))

if os.path.exists('routes/data_explorer.py'):
    app_datas.append(('routes/data_explorer.py', 'routes'))

if os.path.exists('assistant_docs'):
    app_datas.append(('assistant_docs', 'assistant_docs'))

    for md_file in glob.glob('assistant_docs/**/*.md', recursive=True):
        rel_path = os.path.dirname(md_file)
        if (md_file, rel_path) not in app_datas:
            app_datas.append((md_file, rel_path))

if os.path.exists('agent_environments'):

    if os.path.exists('agent_environments/__init__.py'):
        app_datas.append(('agent_environments/__init__.py', 'agent_environments'))

    if os.path.exists('agent_environments/templates'):
        app_datas.append(('agent_environments/templates', 'agent_environments/templates'))

    if os.path.exists('agent_environments/static'):
        app_datas.append(('agent_environments/static', 'agent_environments/static'))

    if os.path.exists('agent_environments/docs'):
        app_datas.append(('agent_environments/docs', 'agent_environments/docs'))

    if os.path.exists('agent_environments/python-bundle'):
        app_datas.append(('agent_environments/python-bundle', 'agent_environments/python-bundle'))

    if os.path.exists('agent_environments/python-bundle-requirements'):
        app_datas.append(('agent_environments/python-bundle-requirements', 'agent_environments/python-bundle-requirements'))

app_datas.extend(all_collected_datas)
app_datas.extend(extra_datas)


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
        'agent_environments',
        'agent_environments.environment_api',
        'agent_environments.environment_manager',
        'agent_environments.environment_config',
        'agent_environments.cloud_config_manager',
        'shared_auth',
        'jwt',
    ] + all_collected_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'user_config.py',
        'user_prompts.py',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

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
