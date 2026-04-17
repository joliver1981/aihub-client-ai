"""
Agent Environment Manager with Cloud Configuration
Complete implementation with all methods
"""

import os
import json
import pyodbc
import venv
import subprocess
import shutil
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, List
import logging
from logging.handlers import WatchedFileHandler
import hashlib
import sys
import threading
import time
from collections import OrderedDict
from .cloud_config_manager import CloudConfigManager
import zipfile
import tempfile
from CommonUtils import rotate_logs_on_startup, get_db_connection_string, get_log_path
import config as cfg
from pathlib import Path


# Configure logging
def setup_logging():
    """Configure logging"""
    logger = logging.getLogger("EnvironmentManager")
    log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
    log_level = getattr(logging, log_level_name, logging.DEBUG)
    logger.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler = WatchedFileHandler(filename=os.getenv('ENVIRONMENT_MANAGER_LOG', get_log_path('environment_manager_log.txt')), encoding='utf-8')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger

rotate_logs_on_startup(os.getenv('ENVIRONMENT_MANAGER_LOG', get_log_path('environment_manager_log.txt')))

logger = setup_logging()


class EnvironmentPackageManager:
    """Package manager that handles local vs system packages"""
    
    def __init__(self, base_path: str, tenant_id: str):
        self.base_path = base_path
        self.connection_string = get_db_connection_string()
        self.tenant_id = tenant_id
        self.logger = logger

    def get_db_connection(self):
        """Create and return a database connection"""
        return pyodbc.connect(self.connection_string)
    
    def _set_tenant_context(self, cursor):
        """Set tenant context for RLS"""
        cursor.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)

    def get_local_packages(self, env_id: str) -> List[Dict]:
        """List packages from database (only user-installed)"""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            self._set_tenant_context(cursor)
            
            cursor.execute("""
                SELECT package_name as name, version
                FROM AgentEnvironmentPackages
                WHERE environment_id = ? AND is_active = 1
                ORDER BY package_name
            """, env_id)
            
            packages = []
            for row in cursor.fetchall():
                packages.append({
                    'name': row.name,
                    'version': row.version
                })
            
            conn.close()
            return packages
            
        except Exception as e:
            self.logger.error(f"Error listing packages: {e}")
            return []
    
    def get_local_packages_from_folder(self, environment_id: str) -> List[Dict]:
        """
        Get ONLY packages actually installed in this environment
        (not inherited from system)
        """
        env_path = os.path.join(self.base_path, environment_id)
        
        if os.name == 'nt':
            pip_path = os.path.join(env_path, 'Scripts', 'pip.exe')
        else:
            pip_path = os.path.join(env_path, 'bin', 'pip')
        
        if not os.path.exists(pip_path):
            return []
        
        try:
            # Use --local flag to get only environment-specific packages
            result = subprocess.run(
                [pip_path, 'list', '--local', '--format=json'],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                packages = json.loads(result.stdout)

                # Filter out infrastructure packages
                INFRASTRUCTURE = cfg.PKG_MGR_INFRASTRUCTURE
                user_packages = [
                    pkg for pkg in packages 
                    if pkg['name'].lower() not in INFRASTRUCTURE
                ]
                return user_packages
            else:
                print(f"Error getting local packages: {result.stderr}")
                return []
                
        except Exception as e:
            print(f"Error listing packages: {e}")
            return []
    
    def get_system_package_count(self, environment_id: str) -> int:
        """
        Get count of system packages available to this environment
        """
        env_path = os.path.join(self.base_path, environment_id)
        
        if os.name == 'nt':
            pip_path = os.path.join(env_path, 'Scripts', 'pip.exe')
        else:
            pip_path = os.path.join(env_path, 'bin', 'pip')
        
        try:
            # Get ALL packages (local + system)
            result_all = subprocess.run(
                [pip_path, 'list', '--format=json'],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            # Get LOCAL packages only
            result_local = subprocess.run(
                [pip_path, 'list', '--local', '--format=json'],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result_all.returncode == 0 and result_local.returncode == 0:
                all_packages = json.loads(result_all.stdout)
                local_packages = json.loads(result_local.stdout)
                
                # System packages = All - Local
                system_count = len(all_packages) - len(local_packages)
                return system_count
            
            return 0
            
        except Exception as e:
            print(f"Error counting system packages: {e}")
            return 0
    
    def get_environment_package_summary(self, environment_id: str) -> Dict:
        """
        Get a complete summary of packages for UI display
        """
        local_packages = self.get_local_packages(environment_id)
        system_count = self.get_system_package_count(environment_id)
        
        return {
            'environment_id': environment_id,
            'local_packages': local_packages,
            'local_count': len(local_packages),
            'system_count': system_count,
            'system_packages_enabled': True,
            'total_available': len(local_packages) + system_count
        }


class HybridPythonBundler:
    """
    Creates a Python bundle with selected pre-installed packages
    and a requirements.txt for packages to be installed at runtime
    """
    
    def __init__(self, source_python=sys.executable, output_dir="python-bundle"):
        self.source_python = source_python
        self.source_prefix = sys.prefix
        self.output_dir = Path(output_dir)
        
    def create_hybrid_bundle(self, 
                            bundled_packages: List[str],
                            requirements_file: str = None,
                            runtime_requirements: List[str] = None) -> bool:
        """
        Create a Python bundle with:
        - Full Python installation
        - Selected packages pre-bundled (modified versions)
        - requirements.txt in the bundle for runtime installation
        
        Args:
            bundled_packages: List of package names to bundle (e.g., ['langchain', 'openai'])
            requirements_file: Path to existing requirements.txt to copy into bundle
            runtime_requirements: List of packages (used if requirements_file not provided)
        """
        
        try:
            print(f"Creating hybrid Python bundle at: {self.output_dir}")
            
            # Clean and create output directory
            if self.output_dir.exists():
                shutil.rmtree(self.output_dir)
            self.output_dir.mkdir(parents=True)
            
            # Step 1: Copy Python core
            self._copy_python_core()
            
            # Step 2: Copy standard library
            self._copy_stdlib()
            
            # Step 3: Copy selected packages (your modified versions)
            self._copy_bundled_packages(bundled_packages)
            
            # Step 4: Copy or create requirements.txt in the bundle
            if requirements_file and os.path.exists(requirements_file):
                # Copy existing requirements.txt
                shutil.copy2(requirements_file, self.output_dir / 'requirements.txt')
                print(f"Copied requirements.txt from {requirements_file}")
            elif runtime_requirements:
                # Create requirements.txt from list
                self._create_requirements_file(runtime_requirements)
            else:
                # Create empty requirements.txt with comments
                self._create_empty_requirements()
            
            # Step 5: Create bundle info
            self._create_bundle_info(bundled_packages)
            
            print("Hybrid bundle created successfully!")
            print(f"Requirements file at: {self.output_dir / 'requirements.txt'}")
            return True
            
        except Exception as e:
            print(f"Error creating hybrid bundle: {e}")
            return False
    
    def _copy_python_core(self):
        """Copy Python executable and core DLLs"""
        print("Copying Python core files...")
        
        # Copy python.exe and pythonw.exe
        for exe in ['python.exe', 'pythonw.exe']:
            src = Path(self.source_prefix) / exe
            if src.exists():
                shutil.copy2(src, self.output_dir / exe)
        
        # Copy Python DLLs
        for item in Path(self.source_prefix).glob('*.dll'):
            shutil.copy2(item, self.output_dir / item.name)
        
        # Copy DLLs directory
        dlls_src = Path(self.source_prefix) / 'DLLs'
        if dlls_src.exists():
            shutil.copytree(dlls_src, self.output_dir / 'DLLs')
    
    def _copy_stdlib(self):
        """Copy entire Python standard library"""
        print("Copying standard library...")
        
        lib_src = Path(self.source_prefix) / 'Lib'
        lib_dst = self.output_dir / 'Lib'
        
        # Copy entire Lib directory for completeness
        shutil.copytree(lib_src, lib_dst, ignore=shutil.ignore_patterns(
            '__pycache__', '*.pyc', '*.pyo', 'test', 'tests'
        ))
    
    def _copy_bundled_packages(self, packages: List[str]):
        """Copy only specified packages from site-packages"""
        print(f"Copying bundled packages: {packages}")
        
        site_src = Path(self.source_prefix) / 'Lib' / 'site-packages'
        site_dst = self.output_dir / 'Lib' / 'site-packages'
        site_dst.mkdir(parents=True, exist_ok=True)
        
        for package_name in packages:
            copied = False
            
            # Try different naming conventions
            for variant in [package_name, package_name.replace('-', '_')]:
                pkg_dir = site_src / variant
                if pkg_dir.exists():
                    print(f"  Bundling {variant}...")
                    shutil.copytree(pkg_dir, site_dst / variant, dirs_exist_ok=True)
                    copied = True
                    break
            
            # Copy .dist-info and .egg-info directories
            for info_pattern in [f"{package_name}*.dist-info", f"{package_name}*.egg-info"]:
                for info_dir in site_src.glob(info_pattern):
                    print(f"  Bundling {info_dir.name}...")
                    shutil.copytree(info_dir, site_dst / info_dir.name, dirs_exist_ok=True)
            
            if not copied:
                print(f"  Warning: Package {package_name} not found in site-packages")
    
    def _create_requirements_file(self, requirements: List[str]):
        """Create requirements.txt from list"""
        print("Creating requirements.txt...")
        
        req_file = self.output_dir / 'requirements.txt'
        with open(req_file, 'w') as f:
            f.write("# Runtime requirements for agent environments\n")
            f.write("# These packages will be installed when new environments are created\n")
            f.write("# They are installed fresh from PyPI to ensure proper dependencies\n\n")
            
            for req in requirements:
                f.write(f"{req}\n")
    
    def _create_empty_requirements(self):
        """Create empty requirements.txt with instructions"""
        req_file = self.output_dir / 'requirements.txt'
        with open(req_file, 'w') as f:
            f.write("# Runtime requirements for agent environments\n")
            f.write("# Add packages here that should be installed fresh in each environment\n")
            f.write("# Format: package==version or package>=version\n\n")
    
    def _create_bundle_info(self, bundled: List[str]):
        """Create bundle information file"""
        info = {
            'python_version': f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            'bundle_type': 'hybrid',
            'bundled_packages': bundled,
            'requirements_file': 'requirements.txt',
            'created_from': self.source_prefix
        }
        
        with open(self.output_dir / 'bundle_info.json', 'w') as f:
            json.dump(info, f, indent=2)


class HybridPythonBundlerLite:
    """
    Creates a Python bundle with selected pre-installed packages
    and a requirements.txt for packages to be installed at runtime
    """
    
    def __init__(self, source_python=sys.executable, output_dir="python-bundle"):
        self.source_python = source_python
        self.source_prefix = sys.prefix
        self.output_dir = Path(output_dir)
        
    def create_hybrid_bundle(self, 
                            bundled_packages: List[str],
                            requirements_file: str = None,
                            runtime_requirements: List[str] = None) -> bool:
        """
        Create a Python bundle with:
        - Full Python installation
        - Selected packages pre-bundled (your modified versions)
        - requirements.txt in the bundle for runtime installation
        """
        
        try:
            print(f"Creating hybrid Python bundle at: {self.output_dir}")
            
            # Clean and create output directory
            if self.output_dir.exists():
                shutil.rmtree(self.output_dir)
            self.output_dir.mkdir(parents=True)
            
            # Step 1: Copy Python core
            self._copy_python_core()
            
            # Step 2: Copy standard library (WITHOUT site-packages)
            self._copy_stdlib()
            
            # Step 3: Copy ONLY selected packages to site-packages
            self._copy_bundled_packages(bundled_packages)
            
            # Step 4: Copy or create requirements.txt in the bundle
            if requirements_file and os.path.exists(requirements_file):
                shutil.copy2(requirements_file, self.output_dir / 'requirements.txt')
                print(f"Copied requirements.txt from {requirements_file}")
            elif runtime_requirements:
                self._create_requirements_file(runtime_requirements)
            else:
                self._create_empty_requirements()
            
            # Step 5: Create bundle info
            self._create_bundle_info(bundled_packages)
            
            print("Hybrid bundle created successfully!")
            print(f"Requirements file at: {self.output_dir / 'requirements.txt'}")
            return True
            
        except Exception as e:
            print(f"Error creating hybrid bundle: {e}")
            return False
        
    def _copy_python_core(self):
        """Copy Python executable and core DLLs"""
        print("Copying Python core files...")
        
        # Copy python.exe and pythonw.exe
        for exe in ['python.exe', 'pythonw.exe']:
            src = Path(self.source_prefix) / exe
            if src.exists():
                shutil.copy2(src, self.output_dir / exe)
        
        # Copy Python DLLs
        for item in Path(self.source_prefix).glob('*.dll'):
            shutil.copy2(item, self.output_dir / item.name)
        
        # Copy DLLs directory
        dlls_src = Path(self.source_prefix) / 'DLLs'
        if dlls_src.exists():
            shutil.copytree(dlls_src, self.output_dir / 'DLLs')
    
    def _copy_stdlib(self):
        """Copy Python standard library WITHOUT site-packages"""
        print("Copying standard library (excluding site-packages)...")
        
        lib_src = Path(self.source_prefix) / 'Lib'
        lib_dst = self.output_dir / 'Lib'
        
        # Copy Lib directory but exclude site-packages
        def ignore_function(dir, files):
            # Ignore site-packages and __pycache__
            ignored = []
            if 'site-packages' in files and dir == str(lib_src):
                ignored.append('site-packages')
            ignored.extend([f for f in files if f == '__pycache__' or f.endswith('.pyc')])
            return ignored
        
        shutil.copytree(lib_src, lib_dst, ignore=ignore_function)
        
        # Create empty site-packages directory
        site_packages = lib_dst / 'site-packages'
        site_packages.mkdir(exist_ok=True)
        
        # Create basic .pth files if needed
        with open(site_packages / 'README.txt', 'w') as f:
            f.write("This directory contains only bundled packages.\n")
            f.write("Other packages will be installed via requirements.txt\n")
    
    def _copy_bundled_packages(self, packages: List[str]):
        """Copy ONLY specified packages to site-packages"""
        print(f"Copying ONLY bundled packages: {packages}")
        
        site_src = Path(self.source_prefix) / 'Lib' / 'site-packages'
        site_dst = self.output_dir / 'Lib' / 'site-packages'
        site_dst.mkdir(parents=True, exist_ok=True)
        
        # Track what we actually copy
        copied_items = []
        
        for package_name in packages:
            copied = False
            
            # Try different naming conventions
            for variant in [package_name, package_name.replace('-', '_')]:
                pkg_dir = site_src / variant
                if pkg_dir.exists():
                    print(f"  Copying package directory: {variant}")
                    shutil.copytree(pkg_dir, site_dst / variant, dirs_exist_ok=True)
                    copied_items.append(variant)
                    copied = True
                    break
            
            # Also copy the package's metadata/dist-info (important for pip)
            for pattern in [f"{package_name}*.dist-info", 
                          f"{package_name}*.egg-info"]:
                for info_dir in site_src.glob(pattern):
                    print(f"  Copying metadata: {info_dir.name}")
                    shutil.copytree(info_dir, site_dst / info_dir.name, dirs_exist_ok=True)
                    copied_items.append(info_dir.name)
            
            # Handle dependencies of the bundled package that are in the same directory
            # For example, langchain might have langchain_core, langchain_community, etc.
            if package_name == 'langchain':
                for related in site_src.glob('langchain*'):
                    if related.name not in copied_items:
                        if related.is_dir():
                            print(f"  Copying related: {related.name}")
                            shutil.copytree(related, site_dst / related.name, dirs_exist_ok=True)
                            copied_items.append(related.name)
            
            if not copied:
                print(f"  WARNING: Package {package_name} not found in site-packages")
        
        # List what was actually copied
        print(f"\nCopied {len(copied_items)} items to site-packages:")
        for item in sorted(copied_items):
            print(f"  - {item}")
        
        # Verify we didn't copy everything
        all_items_in_dst = list(site_dst.iterdir())
        print(f"\nTotal items in bundled site-packages: {len(all_items_in_dst)}")

    def _create_requirements_file(self, requirements: List[str]):
        """Create requirements.txt from list"""
        print("Creating requirements.txt...")
        
        req_file = self.output_dir / 'requirements.txt'
        with open(req_file, 'w') as f:
            f.write("# Runtime requirements for agent environments\n")
            f.write("# These packages will be installed when new environments are created\n")
            f.write("# They are installed fresh from PyPI to ensure proper dependencies\n\n")
            
            for req in requirements:
                f.write(f"{req}\n")
    
    def _create_empty_requirements(self):
        """Create empty requirements.txt with instructions"""
        req_file = self.output_dir / 'requirements.txt'
        with open(req_file, 'w') as f:
            f.write("# Runtime requirements for agent environments\n")
            f.write("# Add packages here that should be installed fresh in each environment\n")
            f.write("# Format: package==version or package>=version\n\n")
    
    def _create_bundle_info(self, bundled: List[str]):
        """Create bundle information file with details"""
        # List what's actually in site-packages
        site_dst = self.output_dir / 'Lib' / 'site-packages'
        actual_packages = [d.name for d in site_dst.iterdir() if d.is_dir() and not d.name.endswith('.dist-info')]
        
        info = {
            'python_version': f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            'bundle_type': 'hybrid',
            'requested_bundled_packages': bundled,
            'actual_bundled_items': actual_packages,
            'requirements_file': 'requirements.txt',
            'created_from': self.source_prefix,
            'created_at': datetime.now().isoformat()
        }
        
        with open(self.output_dir / 'bundle_info.json', 'w') as f:
            json.dump(info, f, indent=2)
        
        print(f"\nBundle info saved. Actual bundled packages: {actual_packages}")
        

class AgentEnvironmentManager:
    """
    Manages isolated Python environments for agents with cloud-based configuration
    Includes hybrid bundling support with pre-installed packages and requirements.txt
    
    Key Design:
    - Configuration comes from cloud database
    - Environments are created lazily
    - Caching with TTL for performance
    - Multi-tenant isolation
    - Hybrid bundling with requirements.txt support
    """
    
    # Curated list of allowed packages (can be overridden from database)
    DEFAULT_ALLOWED_PACKAGES = [
        'pandas', 'numpy', 'requests', 'beautifulsoup4',
        'matplotlib', 'seaborn', 'plotly', 'scikit-learn',
        'openpyxl', 'xlsxwriter', 'lxml', 'pillow',
        'python-dotenv', 'pyyaml', 'jsonschema', 'jinja2',
        'sqlalchemy', 'pymongo', 'redis', 'celery',
        'flask', 'fastapi', 'streamlit', 'dash',
        'tensorflow', 'torch', 'transformers', 'opencv-python'
    ]
    
    BLOCKED_PACKAGES = [
        'subprocess32', 'os-system', 'eval-literal',
        'pyautogui', 'keyboard', 'mouse'
    ]
    
    def __init__(self, tenant_id: int):
        self.connection_string = get_db_connection_string()
        self.tenant_id = tenant_id
        self.logger = logger
        
        # Get configuration from cloud
        self.config_manager = CloudConfigManager(tenant_id)
        self.settings = self.config_manager.get_tenant_settings()
        
        # Check if feature is enabled for this tenant
        if not self.settings.get('environments_enabled', False):
            self.logger.warning(f"Environments not enabled for tenant {tenant_id}")
            self.enabled = False
        else:
            self.enabled = True
            
        # Environment cache with LRU eviction
        self._environment_cache = OrderedDict()
        self._cache_timestamps = {}
        self._cache_lock = threading.Lock()
        
        # Set paths - use absolute path from APP_ROOT
        app_root = os.getenv('APP_ROOT', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.base_path = os.path.join(app_root, 'agent_environments', f'tenant_{tenant_id}')
        os.makedirs(self.base_path, exist_ok=True)

        # Create tenant-level logs folder
        logs_path = os.path.join(self.base_path, 'logs')
        os.makedirs(logs_path, exist_ok=True)

        # Hybrid bundling support
        self.bundle_requirements_path = os.path.join(app_root, 'agent_environments', 'python-bundle-requirements', 'requirements.txt')
        self._detect_bundle_type()
        
        # Start cleanup thread if enabled
        if self.enabled:
            self._start_cleanup_thread()

        # Init package manager
        self.package_manager = EnvironmentPackageManager(self.base_path, self.tenant_id)
    
    def _detect_bundle_type(self):
        """Detect if we're using a hybrid bundle and locate requirements.txt"""
        if getattr(sys, 'frozen', False):
            # Check multiple possible bundle locations
            possible_bundle_dirs = []

            if os.path.exists(os.path.join(os.getenv('APP_ROOT', os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'agent_environments', 'python-bundle-requirements')):
                self.logger.info(f"Found bundle requirements at primary path.")
                possible_bundle_dirs.append(Path(os.getenv('APP_ROOT', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))) / 'agent_environments' / 'python-bundle-requirements')
            else:
                self.logger.info(f"Did not find bundle requirements at primary path, checking alternate path...")
                # If using PyInstaller's _MEIPASS
                if hasattr(sys, '_MEIPASS'):
                    possible_bundle_dirs.append(Path(sys._MEIPASS) / 'python-bundle')
                    possible_bundle_dirs.append(Path(sys._MEIPASS) / 'agent_environments' / 'python-bundle-requirements')
                
                # Installation directory
                app_dir = Path(sys.executable).parent
                possible_bundle_dirs.extend([
                    app_dir / 'python-bundle',
                    app_dir / 'agent_environments' / 'python-bundle-requirements',
                ])
            
            for bundle_dir in possible_bundle_dirs:
                if bundle_dir.exists():
                    # Check for requirements.txt in bundle
                    req_path = bundle_dir / 'requirements.txt'
                    if req_path.exists():
                        self.bundle_requirements_path = req_path
                        self.logger.info(f"Found bundle requirements at: {req_path}")
                        
                    # Check bundle info
                    info_path = bundle_dir / 'bundle_info.json'
                    if info_path.exists():
                        with open(info_path, 'r') as f:
                            info = json.load(f)
                            self.logger.info(f"Bundle type: {info.get('bundle_type')}")
                    break
    
    def get_db_connection(self):
        """Create and return a database connection"""
        return pyodbc.connect(self.connection_string)
    
    def _set_tenant_context(self, cursor):
        """Set tenant context for RLS"""
        if not self.tenant_id:
            self.tenant_id = os.getenv('API_KEY')
        cursor.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)
    
    def create_environment(self, 
                          name: str, 
                          description: str,
                          created_by: int,
                          python_version: str = None) -> Tuple[bool, str, str]:
        """
        Create environment with cloud-based limit checking
        """
        
        # Refresh settings to get latest limits
        self.settings = self.config_manager.get_tenant_settings(force_refresh=True)
        
        # Check if feature is enabled
        if not self.settings.get('environments_enabled', False):
            return False, None, "Agent Environments feature is not enabled for your subscription"
        
        # Check environment limit
        max_environments = self.settings.get('max_environments', 0)
        
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            self._set_tenant_context(cursor)

            self.logger.info(f"Checking for duplicate name {name}")
            
            # Check for duplicate name
            cursor.execute(
                "SELECT COUNT(*) as count FROM AgentEnvironments WHERE name = ? AND is_deleted = 0",
                name
            )
            if cursor.fetchone().count > 0:
                return False, None, f"Environment '{name}' already exists"
            
            self.logger.info(f"Checking limits...")
            
            # Check limit if not unlimited
            if max_environments != -1:  # -1 means unlimited
                cursor.execute("""
                    SELECT COUNT(*) as count
                    FROM AgentEnvironments
                    WHERE is_deleted = 0
                """)
                
                current_count = cursor.fetchone().count
                
                if current_count >= max_environments:
                    tier_name = self.settings.get('tier_display', 'current')
                    return False, None, (
                        f"Environment limit reached ({max_environments} for {tier_name} tier). "
                        f"Please upgrade your subscription to create more environments."
                    )
            
            # Generate unique environment ID
            env_id = self._generate_env_id(name)

            self.logger.info(f"Generated env id {env_id}")

            self.logger.info(f"Inserting agent environment...")
            
            # Insert environment record
            cursor.execute("""
                INSERT INTO AgentEnvironments 
                (environment_id, name, description, python_version, created_by, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
            """, env_id, name, description, 
                python_version or f"{sys.version_info.major}.{sys.version_info.minor}",
                created_by)
            
            # Log usage for billing
            self._log_usage(created_by, env_id, 'create', None, cursor)
            
            conn.commit()
            
            self.logger.info(f"Created environment record: {env_id}")

            # Create the actual virtual environment immediately
            self.logger.info(f"Creating virtual environment for: {env_id}")
            if self._ensure_virtual_environment(env_id):
                return True, env_id, f"Environment '{name}' created and activated successfully"
            else:
                # If venv creation fails, mark as error
                conn = self.get_db_connection()
                cursor = conn.cursor()
                self._set_tenant_context(cursor)
                cursor.execute(
                    "UPDATE AgentEnvironments SET status = 'error' WHERE environment_id = ?",
                    env_id
                )
                conn.commit()
                conn.close()
                return False, env_id, f"Environment record created but activation failed"
            
        except Exception as e:
            self.logger.error(f"Error creating environment: {e}")
            return False, None, str(e)
        finally:
            if 'conn' in locals():
                conn.close()
    
    def list_environments(self, user_id: int = None) -> List[Dict]:
        """
        List environments from database (not file system)
        This is fast as it doesn't load actual virtual environments
        """
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            self._set_tenant_context(cursor)
            
            query = """
                SELECT 
                    e.environment_id,
                    e.name,
                    e.description,
                    e.python_version,
                    e.status,
                    e.created_by,
                    e.created_date,
                    e.usage_count,
                    e.last_used_date,
                    COUNT(DISTINCT p.package_name) as package_count,
                    COUNT(DISTINCT a.agent_id) as agent_count
                FROM AgentEnvironments e
                LEFT JOIN AgentEnvironmentPackages p 
                    ON e.environment_id = p.environment_id 
                    AND p.is_active = 1
                LEFT JOIN AgentEnvironmentAssignments a 
                    ON e.environment_id = a.environment_id 
                    AND a.is_active = 1
                WHERE e.is_deleted = 0
            """
            
            if user_id:
                query += " AND e.created_by = ?"
                cursor.execute(query + " GROUP BY e.environment_id, e.name, e.description, e.python_version, e.status, e.created_by, e.created_date, e.usage_count, e.last_used_date", user_id)
            else:
                cursor.execute(query + " GROUP BY e.environment_id, e.name, e.description, e.python_version, e.status, e.created_by, e.created_date, e.usage_count, e.last_used_date")
            
            environments = []
            for row in cursor.fetchall():
                environments.append({
                    'environment_id': row.environment_id,
                    'name': row.name,
                    'description': row.description,
                    'python_version': row.python_version,
                    'status': row.status,
                    'created_by': row.created_by,
                    'created_date': row.created_date.isoformat() if row.created_date else None,
                    'usage_count': row.usage_count,
                    'last_used_date': row.last_used_date.isoformat() if row.last_used_date else None,
                    'package_count': row.package_count,
                    'agent_count': row.agent_count,
                    'is_loaded': self._is_environment_loaded(row.environment_id)
                })
            
            return environments
            
        except Exception as e:
            self.logger.error(f"Error listing environments: {e}")
            return []
        finally:
            if 'conn' in locals():
                conn.close()
    
    def add_package(self, 
                   env_id: str, 
                   package_name: str,
                   version: str = None,
                   user_id: int = None) -> Tuple[bool, str]:
        """Add package to environment with limit checking"""
        
        # Refresh settings
        self.settings = self.config_manager.get_tenant_settings()
        
        # Check package limit
        max_packages = self.settings.get('max_packages_per_env', 50)
        
        try:
            # Validate package
            if not self._validate_package_name(package_name):
                return False, f"Invalid package name: {package_name}"
            
            # Check against blocked list
            if package_name in self.BLOCKED_PACKAGES:
                return False, f"Package '{package_name}' is blocked for security reasons"
            
            conn = self.get_db_connection()
            cursor = conn.cursor()
            self._set_tenant_context(cursor)
            
            # Check current package count
            if max_packages != -1:
                cursor.execute("""
                    SELECT COUNT(*) as count
                    FROM AgentEnvironmentPackages
                    WHERE environment_id = ? AND is_active = 1
                """, env_id)
                
                current_count = cursor.fetchone().count
                if current_count >= max_packages:
                    return False, f"Package limit reached ({max_packages} per environment)"
            
            # Verify environment exists
            env_path = os.path.join(self.base_path, env_id)
            if not os.path.exists(env_path):
                return False, "Environment not found. It may need to be recreated."
            
            # Load environment into cache
            self._load_environment_to_cache(env_id)
            
            # Get pip path
            pip_path = self._get_pip_path(env_id)
            
            # Install package
            package_spec = f"{package_name}=={version}" if version else package_name
            
            self.logger.info(f"Installing {package_spec} in {env_id}")
            
            result = subprocess.run(
                [pip_path, 'install', package_spec, '--no-cache-dir'],
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode == 0:
                # Check if package already exists
                cursor.execute(
                    "SELECT id FROM AgentEnvironmentPackages WHERE environment_id = ? AND package_name = ?",
                    env_id, package_name
                )
                existing = cursor.fetchone()
                
                if existing:
                    cursor.execute("""
                        UPDATE AgentEnvironmentPackages 
                        SET version = ?, requested_version = ?, installed_date = getutcdate(), is_active = 1
                        WHERE environment_id = ? AND package_name = ?
                    """, version, version, env_id, package_name)
                else:
                    cursor.execute("""
                        INSERT INTO AgentEnvironmentPackages 
                        (environment_id, package_name, version, requested_version, installed_by)
                        VALUES (?, ?, ?, ?, ?)
                    """, env_id, package_name, version, version, user_id or 0)
                
                # Log usage
                self._log_usage(user_id, env_id, 'add_package', package_spec, cursor)
                
                conn.commit()
                
                return True, f"Package {package_name} installed successfully"
            else:
                return False, f"Installation failed: {result.stderr}"
                
        except subprocess.TimeoutExpired:
            return False, "Installation timed out"
        except Exception as e:
            self.logger.error(f"Error adding package: {e}")
            return False, str(e)
        finally:
            if 'conn' in locals():
                conn.close()
    
    def remove_package(self, env_id: str, package_name: str, user_id: int = None) -> Tuple[bool, str]:
        """Remove a package from an environment"""
        try:
            # Ensure environment exists
            if not self._ensure_virtual_environment(env_id):
                return False, "Environment not found"
            
            pip_path = self._get_pip_path(env_id)
            
            result = subprocess.run(
                [pip_path, 'uninstall', package_name, '-y'],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode == 0:
                # Update database
                conn = self.get_db_connection()
                cursor = conn.cursor()
                self._set_tenant_context(cursor)
                
                cursor.execute("""
                    UPDATE AgentEnvironmentPackages 
                    SET is_active = 0
                    WHERE environment_id = ? AND package_name = ?
                """, env_id, package_name)
                
                # Log usage
                self._log_usage(user_id, env_id, 'remove_package', package_name, cursor)
                
                conn.commit()
                conn.close()
                
                return True, f"Package {package_name} removed successfully"
            else:
                return False, f"Removal failed: {result.stderr}"
                
        except Exception as e:
            self.logger.error(f"Error removing package: {e}")
            return False, str(e)
    
    def list_packages(self, env_id: str) -> List[Dict]:
        """List all installed packages in an environment"""
        try:
            # Ensure environment exists
            if not os.path.exists(os.path.join(self.base_path, env_id)):
                return []
            
            pip_path = self._get_pip_path(env_id)
            
            result = subprocess.run(
                [pip_path, 'list', '--format=json'],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                return json.loads(result.stdout)
            return []
            
        except Exception as e:
            self.logger.error(f"Error listing packages: {e}")
            return []
        
    def list_local_packages(self, env_id: str) -> List[Dict]:
        """List all installed packages in an environment"""
        try:
            # Ensure environment exists
            if not os.path.exists(os.path.join(self.base_path, env_id)):
                return []
            
            return self.package_manager.get_local_packages(env_id)
            
        except Exception as e:
            self.logger.error(f"Error listing packages: {e}")
            return []
    
    def clone_environment(self, 
                         source_env_id: str, 
                         new_name: str,
                         created_by: int) -> Tuple[bool, str, str]:
        """Clone an existing environment"""
        try:
            # First create the new environment
            success, new_env_id, message = self.create_environment(
                name=new_name,
                description=f"Clone of environment {source_env_id}",
                created_by=created_by
            )
            
            if not success:
                return False, None, message
            
            # Get packages from source environment
            conn = self.get_db_connection()
            cursor = conn.cursor()
            self._set_tenant_context(cursor)
            
            cursor.execute("""
                SELECT package_name, version
                FROM AgentEnvironmentPackages
                WHERE environment_id = ? AND is_active = 1
            """, source_env_id)
            
            packages = cursor.fetchall()
            conn.close()
            
            # Install packages in new environment
            for package in packages:
                self.add_package(new_env_id, package.package_name, package.version, created_by)
            
            return True, new_env_id, f"Environment cloned successfully"
            
        except Exception as e:
            self.logger.error(f"Error cloning environment: {e}")
            return False, None, str(e)
    
    def delete_environment(self, env_id: str, user_id: int = None) -> Tuple[bool, str]:
        """Delete an environment (soft delete)"""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            self._set_tenant_context(cursor)
            
            # Check if environment is in use
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM AgentEnvironmentAssignments
                WHERE environment_id = ? AND is_active = 1
            """, env_id)
            
            if cursor.fetchone().count > 0:
                return False, "Environment is currently assigned to agents. Unassign first."
            
            # Soft delete in database
            cursor.execute("""
                UPDATE AgentEnvironments
                SET is_deleted = 1, updated_date = getutcdate()
                WHERE environment_id = ?
            """, env_id)
            
            # Log usage
            self._log_usage(user_id, env_id, 'delete', None, cursor)
            
            conn.commit()
            
            # Remove physical environment if exists
            env_path = os.path.join(self.base_path, env_id)
            if os.path.exists(env_path):
                shutil.rmtree(env_path)
            
            # Remove from cache
            with self._cache_lock:
                if env_id in self._environment_cache:
                    del self._environment_cache[env_id]
                if env_id in self._cache_timestamps:
                    del self._cache_timestamps[env_id]
            
            return True, "Environment deleted successfully"
            
        except Exception as e:
            self.logger.error(f"Error deleting environment: {e}")
            return False, str(e)
        finally:
            if 'conn' in locals():
                conn.close()
    
    def get_environment_for_agent(self, agent_id: int) -> Optional[str]:
        """
        Get environment for an agent (loads if needed)
        Returns Python executable path or None
        """
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            self._set_tenant_context(cursor)
            
            # Get assigned environment
            cursor.execute("""
                SELECT environment_id 
                FROM AgentEnvironmentAssignments 
                WHERE agent_id = ? AND is_active = 1
            """, agent_id)
            
            row = cursor.fetchone()
            if not row:
                return None
            
            env_id = row.environment_id
            
            # Update usage stats
            cursor.execute("""
                UPDATE AgentEnvironments 
                SET usage_count = usage_count + 1, 
                    last_used_date = getutcdate() 
                WHERE environment_id = ?
            """, env_id)
            conn.commit()
            
            # Load to cache
            self._load_environment_to_cache(env_id)
            
            # Return Python executable path
            return self.get_python_executable(env_id)
            
        except Exception as e:
            self.logger.error(f"Error getting environment for agent: {e}")
            return None
        finally:
            if 'conn' in locals():
                conn.close()
    
    def assign_environment_to_agent(self, 
                                  env_id: str, 
                                  agent_id: int,
                                  user_id: int) -> Tuple[bool, str]:
        """Assign an environment to an agent"""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            self._set_tenant_context(cursor)
            
            # Deactivate any existing assignment
            cursor.execute("""
                UPDATE AgentEnvironmentAssignments 
                SET is_active = 0 
                WHERE agent_id = ? AND is_active = 1
            """, agent_id)
            
            # Create new assignment
            cursor.execute("""
                INSERT INTO AgentEnvironmentAssignments 
                (agent_id, environment_id, assigned_by)
                VALUES (?, ?, ?)
            """, agent_id, env_id, user_id)
            
            # Log usage
            self._log_usage(user_id, env_id, 'assign_agent', f'agent_{agent_id}', cursor)
            
            conn.commit()
            
            return True, "Environment assigned successfully"
            
        except Exception as e:
            self.logger.error(f"Error assigning environment: {e}")
            return False, str(e)
        finally:
            if 'conn' in locals():
                conn.close()
    
    def get_python_executable(self, env_id: str) -> Optional[str]:
        """Get Python executable path for an environment"""
        env_path = os.path.join(self.base_path, env_id)
        
        if os.name == 'nt':  # Windows
            python_path = os.path.join(env_path, 'Scripts', 'python.exe')
        else:  # Unix-like
            python_path = os.path.join(env_path, 'bin', 'python')
        
        return python_path if os.path.exists(python_path) else None
    
    def _get_base_python_executable(self) -> str:
        """Get the actual Python executable, not the PyInstaller exe"""
        
        # First try bundled Python (hybrid approach)
        bundled_python = self._get_bundled_python()
        if bundled_python:
            self.logger.info(f"Using bundled Python: {bundled_python}")
            return bundled_python
        
        # Fallback to sys._base_executable
        if hasattr(sys, '_base_executable') and sys._base_executable and os.path.exists(sys._base_executable) and str(sys._base_executable).lower().__contains__('python'):
            self.logger.info(f"Using sys._base_executable: {sys._base_executable}")
            return sys._base_executable
        
        raise RuntimeError("Cannot find Python executable for venv creation. No system Python or bundled Python found.")
        
    def _get_bundled_python(self) -> Optional[str]:
        """Get path to bundled Python executable (hybrid bundling support)"""
        if os.path.exists(os.path.join(os.getenv('APP_ROOT'), 'agent_environments', 'python-bundle')):
            self.logger.info(f"Found python in primary location, preparing bundled python...")
            python_path_main = Path(os.path.join(os.getenv('APP_ROOT'), 'agent_environments', 'python-bundle', 'python.exe'))
            self._prepare_bundled_python(python_path_main.parent)
            return str(python_path_main)
            
        self.logger.info(f"Found NOT python in primary location, checking alternate locations...")

        # Check multiple possible locations
        possible_locations = []

        possible_locations.append(
                Path(cfg.ENV_BUNDLE_PATH) / 'python.exe'
            )
        
        # If using PyInstaller's _MEIPASS
        if hasattr(sys, '_MEIPASS'):
            possible_locations.append(
                Path(sys._MEIPASS) / 'python-bundle' / 'python.exe'
            )
            possible_locations.append(
                Path(sys._MEIPASS) / 'agent_environments' / 'python-bundle' / 'python.exe'
            )
        
        # Installation directory
        app_dir = Path(sys.executable).parent
        possible_locations.extend([
            app_dir / 'python-bundle' / 'python.exe',
            app_dir / 'agent_environments' / 'python-bundle' / 'python.exe',
        ])
        
        for python_path in possible_locations:
            self.logger.info(f"Checking python path: {python_path}")
            if python_path.exists():
                self.logger.info(f"Path exists, preparing bundled python...")
                # Prepare the bundled Python
                self._prepare_bundled_python(python_path.parent)
                self.logger.info(f"Found bundled Python at: {python_path}")
                return str(python_path)
        
        self.logger.info(f"No python bundle found.")
        return None
    
    def _prepare_bundled_python(self, python_dir: str):
        """Prepare bundled Python for venv creation by removing ._pth file"""
        # The embedded Python has a python3XX._pth file that restricts imports
        # We need to rename/remove it to allow venv to work properly
        pth_files = [f for f in os.listdir(python_dir) if f.endswith('._pth')]
        
        for pth_file in pth_files:
            pth_path = os.path.join(python_dir, pth_file)
            backup_path = pth_path + '.backup'
            
            # Only rename if not already done
            if os.path.exists(pth_path) and not os.path.exists(backup_path):
                try:
                    os.rename(pth_path, backup_path)
                    self.logger.info(f"Renamed {pth_file} to allow venv creation")
                except Exception as e:
                    self.logger.warning(f"Could not rename {pth_file}: {e}")
    
    def _ensure_virtual_environment(self, env_id: str) -> bool:
        """
        Create virtual environment WITHOUT system-site-packages and copy bundled packages
        Supports hybrid bundling approach
        """
        env_path = os.path.join(self.base_path, env_id)
        
        if os.path.exists(env_path):
            return True
        
        try:
            self.logger.info(f"Creating virtual environment: {env_id}")
            
            python_exe = self._get_base_python_executable()
            self.logger.info(f"Using Python executable: {python_exe}")
            
            # Create isolated venv (NO --system-site-packages for hybrid approach)
            cmd = [python_exe, '-m', 'venv', env_path, '--clear']
            self.logger.info(f"Executing command: {cmd}")
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                self.logger.error(f"venv creation failed: {result.stderr}")
                
                # Try alternative method if venv module fails
                if 'No module named venv' in result.stderr or 'ensurepip' in result.stderr:
                    self.logger.info("Standard venv failed, trying alternative method...")
                    if self._create_venv_manual(env_path, python_exe):
                        self.logger.info("Successfully created venv using manual method")
                    else:
                        raise Exception("Failed to create venv with manual method")
                else:
                    raise Exception(f"Failed to create venv: {result.stderr}")

            # Verify the environment was created properly
            if os.name == 'nt':
                python_check = os.path.join(env_path, 'Scripts', 'python.exe')
                pip_check = os.path.join(env_path, 'Scripts', 'pip.exe')
            else:
                python_check = os.path.join(env_path, 'bin', 'python')
                pip_check = os.path.join(env_path, 'bin', 'pip')
            
            if not os.path.exists(python_check):
                raise Exception(f"Virtual environment creation failed - python executable not found")
            
            # Ensure pip is available
            if not os.path.exists(pip_check):
                self.logger.warning("pip not found, bootstrapping...")
                self._bootstrap_pip(python_check)
            
            # Copy bundled packages to venv (for frozen builds with hybrid bundling)
            self.logger.info("Copying bundled packages to venv...")
            self._copy_bundled_packages_to_venv(env_id)
            
            # Install base packages (pip, setuptools, wheel)
            if cfg.PKG_MGR_INSTALL_BASE:
                self.logger.info("Installing base packages...")
                self._install_base_packages(env_id)
            
            # Install from bundle's requirements.txt if it exists (hybrid bundling)
            if self.bundle_requirements_path and os.path.exists(self.bundle_requirements_path):
                self.logger.info(f"Installing from bundle requirements: {self.bundle_requirements_path}")
                self._install_from_bundle_requirements(env_id)
            
            # Update status in database
            conn = self.get_db_connection()
            cursor = conn.cursor()
            self._set_tenant_context(cursor)
            
            cursor.execute(
                "UPDATE AgentEnvironments SET status = 'active' WHERE environment_id = ?",
                env_id
            )
            conn.commit()
            conn.close()
            
            self.logger.info(f"Environment {env_id} created successfully and marked as active")
            return True
            
        except Exception as e:
            self.logger.error(f"Error creating virtual environment: {e}")
            # Clean up partial environment if it exists
            if os.path.exists(env_path):
                try:
                    shutil.rmtree(env_path)
                except:
                    pass
            return False
    
    def _copy_bundled_packages_to_venv(self, env_id: str):
        """
        Copy bundled packages directly INTO the venv's site-packages (hybrid bundling)
        """
        env_path = os.path.join(self.base_path, env_id)
        
        # Get bundled Python location
        bundled_python = self._get_bundled_python()
        if not bundled_python:
            self.logger.warning("No bundled Python found, skipping package copy")
            return
        
        bundle_site_packages = Path(bundled_python).parent / 'Lib' / 'site-packages'
        
        if not bundle_site_packages.exists():
            self.logger.warning(f"Bundle site-packages not found at {bundle_site_packages}")
            return
        
        # Destination: venv's site-packages
        if os.name == 'nt':
            venv_site_packages = Path(env_path) / 'Lib' / 'site-packages'
        else:
            python_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
            venv_site_packages = Path(env_path) / 'lib' / python_version / 'site-packages'
        
        # Create site-packages if it doesn't exist
        venv_site_packages.mkdir(parents=True, exist_ok=True)
        
        # Copy your modified packages (langchain, openai, and their related packages)
        copied_count = 0
        for item in bundle_site_packages.iterdir():
            # Copy all bundled packages
            self.logger.info(f"Copying bundled package: {item.name}")
            try:
                if item.is_dir():
                    dest = venv_site_packages / item.name
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(item, dest)
                    copied_count += 1
                else:
                    shutil.copy2(item, venv_site_packages / item.name)
            except Exception as e:
                self.logger.error(f"Failed to copy {item.name}: {e}")
        
        self.logger.info(f"Copied {copied_count} bundled packages to venv")
    
    def _install_from_bundle_requirements(self, env_id: str):
        """Install packages from the bundle's requirements.txt (hybrid bundling)"""
        pip_path = self._get_pip_path(env_id)
        
        if not os.path.exists(pip_path):
            self.logger.error(f"pip not found at {pip_path}")
            return
        
        # Read requirements file
        with open(self.bundle_requirements_path, 'r') as f:
            requirements = [line.strip() for line in f.readlines() 
                        if line.strip() and not line.startswith('#')]
        
        self.logger.info(f"Installing {len(requirements)} packages from requirements.txt")
        
        # Try to install all at once first
        result = subprocess.run(
            [pip_path, 'install', '-r', str(self.bundle_requirements_path), '--no-cache-dir'],
            capture_output=True,
            text=True,
            timeout=600
        )
        
        if result.returncode != 0:
            self.logger.warning(f"Bulk install failed: {result.stderr}")
            self.logger.info("Trying one-by-one installation...")
            
            failed_packages = []
            for req in requirements:
                self.logger.info(f"Installing: {req}")
                result = subprocess.run(
                    [pip_path, 'install', req, '--no-cache-dir'],
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                
                if result.returncode != 0:
                    self.logger.warning(f"Failed to install {req}: {result.stderr}")
                    failed_packages.append(req)
                else:
                    self.logger.info(f"Successfully installed {req}")
            
            if failed_packages:
                self.logger.error(f"Failed packages: {', '.join(failed_packages)}")
        else:
            self.logger.info("Successfully installed all runtime requirements")
    
    def _create_venv_manual(self, env_path: str, python_exe: str) -> bool:
        """Manually create a minimal virtual environment structure"""
        try:
            # Create directory structure
            os.makedirs(env_path, exist_ok=True)
            
            if os.name == 'nt':
                scripts_dir = os.path.join(env_path, 'Scripts')
                os.makedirs(scripts_dir, exist_ok=True)
                
                # Copy python.exe to Scripts
                shutil.copy2(python_exe, os.path.join(scripts_dir, 'python.exe'))
                
                # Create a pyvenv.cfg file
                cfg_content = f"""home = {os.path.dirname(python_exe)}
                include-system-site-packages = false
                version = {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}
                """
                with open(os.path.join(env_path, 'pyvenv.cfg'), 'w') as f:
                    f.write(cfg_content)
                
                # Bootstrap pip
                venv_python = os.path.join(scripts_dir, 'python.exe')
                self._bootstrap_pip(venv_python)
                
                return True
            else:
                # Unix-like implementation would go here
                bin_dir = os.path.join(env_path, 'bin')
                os.makedirs(bin_dir, exist_ok=True)
                # ... implement Unix version if needed
                
        except Exception as e:
            self.logger.error(f"Manual venv creation failed: {e}")
            return False
    
    def _bootstrap_pip(self, python_exe: str):
        """Bootstrap pip in the virtual environment"""
        try:
            # First try ensurepip
            result = subprocess.run(
                [python_exe, '-m', 'ensurepip', '--default-pip'],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                self.logger.info("Successfully bootstrapped pip using ensurepip")
                return
            
            # If ensurepip fails, try get-pip.py
            self.logger.info("ensurepip failed, trying get-pip.py...")
            
            import urllib.request
            import tempfile
            
            with tempfile.NamedTemporaryFile(suffix='.py', delete=False) as tmp:
                urllib.request.urlretrieve('https://bootstrap.pypa.io/get-pip.py', tmp.name)
                
                result = subprocess.run(
                    [python_exe, tmp.name],
                    capture_output=True,
                    text=True
                )
                
                os.unlink(tmp.name)
                
                if result.returncode == 0:
                    self.logger.info("Successfully installed pip using get-pip.py")
                else:
                    self.logger.error(f"Failed to install pip: {result.stderr}")
                    
        except Exception as e:
            self.logger.error(f"Failed to bootstrap pip: {e}")
    
    def _generate_env_id(self, name: str) -> str:
        """Generate unique environment ID"""
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        hash_input = f"{name}_{timestamp}_{self.tenant_id}"
        return hashlib.md5(hash_input.encode()).hexdigest()[:12]
    
    def _get_pip_path(self, env_id: str) -> str:
        """Get pip executable path"""
        env_path = os.path.join(self.base_path, env_id)
        if os.name == 'nt':
            return os.path.join(env_path, 'Scripts', 'pip.exe')
        else:
            return os.path.join(env_path, 'bin', 'pip')
    
    def _validate_package_name(self, package_name: str) -> bool:
        """Validate package name"""
        import re
        pattern = r'^[a-zA-Z0-9\-_.]+$'
        return bool(re.match(pattern, package_name))
    
    def _install_base_packages(self, env_id: str):
        """Install essential base packages"""
        base_packages = ['pip', 'setuptools', 'wheel']
        pip_path = self._get_pip_path(env_id)
        
        for package in base_packages:
            self.logger.info(f"Installing package {package}...")
            print(f"Installing package {package}...")
            subprocess.run(
                [pip_path, 'install', '--upgrade', package],
                capture_output=True,
                timeout=60
            )
    
    def _log_usage(self, user_id: int, env_id: str, action: str, details: str, cursor):
        """Log usage for billing"""
        cursor.execute("""
            INSERT INTO AgentEnvironmentUsage 
            (user_id, environment_id, action, details)
            VALUES (?, ?, ?, ?)
        """, user_id or 0, env_id, action, details)
    
    def _load_environment_to_cache(self, env_id: str):
        """Load environment to cache with TTL"""
        with self._cache_lock:
            # Move to end (most recently used)
            if env_id in self._environment_cache:
                self._environment_cache.move_to_end(env_id)
            else:
                self._environment_cache[env_id] = True
                
            self._cache_timestamps[env_id] = datetime.now()
            
            # Get max concurrent environments from settings
            max_concurrent = self.settings.get('max_concurrent_environments', 10)
            
            # Evict old environments if cache is full
            while len(self._environment_cache) > max_concurrent:
                oldest_env = next(iter(self._environment_cache))
                del self._environment_cache[oldest_env]
                del self._cache_timestamps[oldest_env]
                self.logger.info(f"Evicted environment from cache: {oldest_env}")
    
    def _is_environment_loaded(self, env_id: str) -> bool:
        """Check if environment is in cache"""
        with self._cache_lock:
            return env_id in self._environment_cache
    
    def _cleanup_stale_cache(self):
        """Remove stale entries from cache"""
        with self._cache_lock:
            current_time = datetime.now()
            cache_ttl = self.settings.get('cache_timeout_minutes', 30)
            timeout = timedelta(minutes=cache_ttl)
            
            stale_envs = []
            for env_id, timestamp in self._cache_timestamps.items():
                if current_time - timestamp > timeout:
                    stale_envs.append(env_id)
            
            for env_id in stale_envs:
                del self._environment_cache[env_id]
                del self._cache_timestamps[env_id]
                self.logger.info(f"Removed stale environment from cache: {env_id}")
    
    def _start_cleanup_thread(self):
        """Start background thread for cache cleanup"""
        def cleanup_worker():
            while True:
                time.sleep(300)  # Run every 5 minutes
                try:
                    # Refresh settings periodically
                    self.settings = self.config_manager.get_tenant_settings()
                    # Cleanup cache
                    self._cleanup_stale_cache()
                except Exception as e:
                    self.logger.error(f"Error in cleanup thread: {e}")
        
        thread = threading.Thread(target=cleanup_worker, daemon=True)
        thread.start()

    def export_environment(self, env_id: str, user_id: int) -> Tuple[bool, str, bytes]:
        """
        Export an environment to a portable ZIP package
        Returns: (success, message, zip_data)
        """
        try:
            # Get environment details
            conn = self.get_db_connection()
            cursor = conn.cursor()
            self._set_tenant_context(cursor)
            
            # Fetch environment metadata
            cursor.execute("""
                SELECT name, description, python_version, created_date
                FROM AgentEnvironments
                WHERE environment_id = ? AND is_deleted = 0
            """, env_id)
            
            env_data = cursor.fetchone()
            if not env_data:
                return False, "Environment not found", None
            
            # Fetch installed packages
            cursor.execute("""
                SELECT package_name, version, requested_version
                FROM AgentEnvironmentPackages
                WHERE environment_id = ? AND is_active = 1
                ORDER BY package_name
            """, env_id)
            
            packages = []
            requirements_txt = ""
            for row in cursor.fetchall():
                packages.append({
                    "name": row.package_name,
                    "version": row.version,
                    "requested_version": row.requested_version
                })
                # Build requirements.txt format
                if row.requested_version:
                    requirements_txt += f"{row.package_name}=={row.requested_version}\n"
                elif row.version:
                    requirements_txt += f"{row.package_name}=={row.version}\n"
                else:
                    requirements_txt += f"{row.package_name}\n"
            
            conn.close()

            ENV_VERSION = "1.0"
            
            # Create manifest
            manifest = {
                "version": ENV_VERSION,
                "export_date": datetime.now().isoformat(),
                "environment": {
                    "name": env_data.name,
                    "description": env_data.description,
                    "python_version": env_data.python_version,
                    "created_date": env_data.created_date.isoformat() if env_data.created_date else None,
                    "original_id": env_id
                },
                "packages": packages,
                "export_metadata": {
                    "platform": os.name
                }
            }
            
            # Create ZIP package
            temp_dir = tempfile.mkdtemp()
            try:
                # Write manifest
                manifest_path = os.path.join(temp_dir, "manifest.json")
                with open(manifest_path, 'w') as f:
                    json.dump(manifest, f, indent=2)
                
                # Write requirements.txt
                requirements_path = os.path.join(temp_dir, "requirements.txt")
                with open(requirements_path, 'w') as f:
                    f.write(requirements_txt)
                
                # Optional: Get pip freeze output for exact versions
                if os.path.exists(os.path.join(self.base_path, env_id)):
                    pip_path = self._get_pip_path(env_id)
                    result = subprocess.run(
                        [pip_path, 'freeze'],
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    if result.returncode == 0:
                        freeze_path = os.path.join(temp_dir, "requirements-freeze.txt")
                        with open(freeze_path, 'w') as f:
                            f.write(result.stdout)
                
                # Create ZIP file
                zip_path = os.path.join(temp_dir, f"{env_data.name}_export.zip")
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for root, dirs, files in os.walk(temp_dir):
                        for file in files:
                            if file.endswith('.zip'):
                                continue
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, temp_dir)
                            zipf.write(file_path, arcname)
                
                # Read ZIP data
                with open(zip_path, 'rb') as f:
                    zip_data = f.read()
                
                # Log the export
                conn = self.get_db_connection()
                cursor = conn.cursor()
                self._set_tenant_context(cursor)
                self._log_usage(user_id, env_id, 'export', f'Exported {len(packages)} packages', cursor)
                conn.commit()
                conn.close()
                
                return True, f"Environment exported successfully ({len(packages)} packages)", zip_data
                
            finally:
                # Cleanup temp directory
                shutil.rmtree(temp_dir)
                
        except Exception as e:
            self.logger.error(f"Error exporting environment: {e}")
            return False, str(e), None
    
    def import_environment(self, 
                          zip_data: bytes, 
                          user_id: int,
                          new_name: str = None,
                          skip_packages: bool = False) -> Tuple[bool, str, str]:
        """
        Import an environment from a ZIP package
        
        Args:
            zip_data: The ZIP file content as bytes
            user_id: User performing the import
            new_name: Optional new name for the environment (uses original if not provided)
            skip_packages: If True, only creates environment without installing packages
            
        Returns: (success, env_id, message)
        """
        temp_dir = tempfile.mkdtemp()
        
        try:
            # Extract ZIP
            zip_path = os.path.join(temp_dir, "import.zip")
            with open(zip_path, 'wb') as f:
                f.write(zip_data)
            
            self.logger.info(f"Extracting files...")
            extract_dir = os.path.join(temp_dir, "extracted")
            with zipfile.ZipFile(zip_path, 'r') as zipf:
                zipf.extractall(extract_dir)
            
            # Load manifest
            manifest_path = os.path.join(extract_dir, "manifest.json")
            if not os.path.exists(manifest_path):
                return False, None, "Invalid package: manifest.json not found"
            
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
            
            # Validate manifest version
            if manifest.get('version') != '1.0':
                return False, None, f"Unsupported manifest version: {manifest.get('version')}"
            
            # Extract environment info
            env_info = manifest.get('environment', {})
            original_name = env_info.get('name', 'Imported Environment')
            
            self.logger.info(f"Verifying name...")
            # Use provided name or generate unique name
            if new_name:
                env_name = new_name
            else:
                # Check if name exists and make unique
                env_name = self._generate_unique_name(original_name)
            
            self.logger.info(f"Creating environment...")
            # Create new environment
            success, env_id, message = self.create_environment(
                name=env_name,
                description=env_info.get('description', f'Imported from {original_name}'),
                created_by=user_id,
                python_version=env_info.get('python_version')
            )
            self.logger.info(f"Finished creating environment...")
            if not success:
                return False, None, message
            
            # Install packages if requested
            if not skip_packages:
                # Check for requirements file
                requirements_path = os.path.join(extract_dir, "requirements.txt")
                freeze_path = os.path.join(extract_dir, "requirements-freeze.txt")
                
                # Prefer freeze file for exact versions
                if os.path.exists(requirements_path):
                    install_path = requirements_path
                elif os.path.exists(freeze_path):
                    install_path = freeze_path
                else:
                    install_path = None
                
                if install_path:
                    self.logger.info(f"Installing from requirements...")
                    success_msg = self._install_from_requirements(env_id, install_path, user_id)
                    if not success_msg[0]:
                        # Log warning but don't fail the import
                        self.logger.warning(f"Some packages failed to install: {success_msg[1]}")
                else:
                    # Install packages one by one from manifest
                    packages = manifest.get('packages', [])
                    failed_packages = []
                    
                    for pkg in packages:
                        pkg_name = pkg.get('name')
                        pkg_version = pkg.get('requested_version') or pkg.get('version')
                        
                        success_pkg, msg = self.add_package(env_id, pkg_name, pkg_version, user_id)
                        if not success_pkg:
                            failed_packages.append(f"{pkg_name}: {msg}")
                    
                    if failed_packages:
                        self.logger.warning(f"Failed to install packages: {', '.join(failed_packages)}")
            
            self.logger.info(f"Logging import...")
            # Log the import
            conn = self.get_db_connection()
            cursor = conn.cursor()
            self._set_tenant_context(cursor)
            self._log_usage(user_id, env_id, 'import', f'Imported from {original_name}', cursor)
            conn.commit()
            conn.close()
            
            package_count = len(manifest.get('packages', []))
            self.logger.info(f"Environment imported successfully ({package_count} packages)")
            return True, env_id, f"Environment imported successfully ({package_count} packages)"
            
        except Exception as e:
            self.logger.error(f"Error importing environment: {e}")
            return False, None, str(e)
        finally:
            # Cleanup temp directory
            shutil.rmtree(temp_dir)
    
    def _generate_unique_name(self, base_name: str) -> str:
        """Generate a unique environment name"""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        self._set_tenant_context(cursor)
        
        # Check if base name exists
        cursor.execute("""
            SELECT COUNT(*) as count 
            FROM AgentEnvironments 
            WHERE name = ? AND is_deleted = 0
        """, base_name)
        
        if cursor.fetchone().count == 0:
            conn.close()
            return base_name
        
        # Generate unique name with counter
        counter = 1
        while True:
            new_name = f"{base_name}_{counter}"
            cursor.execute("""
                SELECT COUNT(*) as count 
                FROM AgentEnvironments 
                WHERE name = ? AND is_deleted = 0
            """, new_name)
            
            if cursor.fetchone().count == 0:
                conn.close()
                return new_name
            
            counter += 1
            if counter > 100:  # Safety limit
                conn.close()
                return f"{base_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    def _install_from_requirements(self, env_id: str, requirements_path: str, user_id: int) -> Tuple[bool, str]:
        """Install packages from requirements.txt file"""
        try:
            # Ensure environment exists
            self.logger.info(f"Ensuring virtual environment...")
            if not self._ensure_virtual_environment(env_id):
                return False, "Failed to create virtual environment"
            
            pip_path = self._get_pip_path(env_id)
            
            self.logger.info(f"Installing requirements file... {requirements_path}")
            print(f"Installing requirements file... {requirements_path}")
            # Install from requirements file
            result = subprocess.run(
                [pip_path, 'install', '-r', requirements_path, '--no-cache-dir'],
                capture_output=True,
                text=True,
                timeout=600  # 10 minutes timeout for bulk install
            )
            self.logger.info(f"Requirements install result: {result}")
            print(f"Requirements install result: {result}")
            if result.returncode == 0:
                self.logger.info(f"Syncing packages to database...")
                # Parse installed packages and update database
                self._sync_packages_to_database(env_id, user_id)
                return True, "All packages installed successfully"
            else:
                self.logger.info(f"Installing packages one by one...")
                print(f"Installing packages one by one...")
                # Try to install packages one by one
                with open(requirements_path, 'r') as f:
                    requirements = f.readlines()
                
                failed = []
                for req in requirements:
                    self.logger.info(f"Installing {req}...")
                    print(f"Installing {req}...")
                    req = req.strip()
                    if req and not req.startswith('#'):
                        result = subprocess.run(
                            [pip_path, 'install', req, '--no-cache-dir'],
                            capture_output=True,
                            text=True,
                            timeout=300
                        )
                        if result.returncode != 0:
                            failed.append(req)
                
                self.logger.info(f"Syncing packages to database...")
                print(f"Syncing packages to database...")
                # Sync whatever was installed
                self._sync_packages_to_database(env_id, user_id)
                
                if failed:
                    self.logger.warning(f"Failed to install: {', '.join(failed)}")
                    return False, f"Failed to install: {', '.join(failed)}"
                return True, "Packages installed with some warnings"
                
        except Exception as e:
            return False, str(e)
    
    def _sync_packages_to_database(self, env_id: str, user_id: int):
        """Sync installed packages from environment to database"""
        try:
            # Get current packages from pip
            packages = self.package_manager.get_local_packages(env_id)
            
            conn = self.get_db_connection()
            cursor = conn.cursor()
            self._set_tenant_context(cursor)
            
            for pkg in packages:
                # Check if package exists in database
                cursor.execute("""
                    SELECT id FROM AgentEnvironmentPackages 
                    WHERE environment_id = ? AND package_name = ?
                """, env_id, pkg['name'])
                
                existing = cursor.fetchone()
                
                if not existing:
                    # Add to database
                    cursor.execute("""
                        INSERT INTO AgentEnvironmentPackages 
                        (environment_id, package_name, version, requested_version, 
                         installed_date, installed_by, is_active)
                        VALUES (?, ?, ?, ?, getutcdate(), ?, 1)
                    """, env_id, pkg['name'], pkg['version'], pkg['version'], user_id)
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            self.logger.error(f"Error syncing packages to database: {e}")

    def verify_and_repair_environment(self, env_id: str) -> Tuple[bool, str]:
        """Verify environment exists and repair if needed"""
        env_path = os.path.join(self.base_path, env_id)
        
        if os.path.exists(env_path):
            return True, "Environment is healthy"
        
        # Environment missing on disk but exists in DB
        self.logger.warning(f"Environment {env_id} missing on disk, recreating...")
        
        try:
            # Get environment details from DB
            conn = self.get_db_connection()
            cursor = conn.cursor()
            self._set_tenant_context(cursor)
            
            cursor.execute(
                "SELECT status FROM AgentEnvironments WHERE environment_id = ?",
                env_id
            )
            row = cursor.fetchone()
            
            if not row:
                return False, "Environment not found in database"
            
            # Recreate the virtual environment
            if self._ensure_virtual_environment(env_id):
                return True, "Environment recreated successfully"
            else:
                # Mark as error in DB
                cursor.execute(
                    "UPDATE AgentEnvironments SET status = 'error' WHERE environment_id = ?",
                    env_id
                )
                conn.commit()
                return False, "Failed to recreate environment"
                
        except Exception as e:
            self.logger.error(f"Error repairing environment: {e}")
            return False, str(e)
        finally:
            if 'conn' in locals():
                conn.close()


    def create_environment_with_progress(self, 
                                        name: str, 
                                        description: str,
                                        created_by: int,
                                        python_version: str = None,
                                        progress_callback=None) -> Tuple[bool, str, str]:
        """
        Create environment with progress reporting
        progress_callback: function(step, progress, message)
        """
        def count_packages():
            """Count packages in site-packages folder"""
            env_path = os.path.join(self.base_path, env_id)
            if os.name == 'nt':
                site_pkg = os.path.join(env_path, 'Lib', 'site-packages')
            else:
                site_pkg = os.path.join(env_path, 'lib', f'python{sys.version_info.major}.{sys.version_info.minor}', 'site-packages')
            
            if not os.path.exists(site_pkg):
                return 0
            
            # Count folders (exclude metadata folders)
            count = 0
            for item in os.listdir(site_pkg):
                if os.path.isdir(os.path.join(site_pkg, item)):
                    if not item.endswith('.dist-info') and item != '__pycache__':
                        count += 1
            return count
        
        def report_progress(step, progress, message):
            """Helper to report progress if callback provided"""
            if progress_callback:
                progress_callback(step, progress, message)
            self.logger.info(f"Progress: {progress}% - {step}: {message}")
        
        # Start progress reporting
        report_progress("Initialization", 0, "Starting environment creation...")
        
        # Refresh settings to get latest limits
        report_progress("Settings", 5, "Loading subscription settings...")
        self.settings = self.config_manager.get_tenant_settings(force_refresh=True)
        
        # Check if feature is enabled
        if not self.settings.get('environments_enabled', False):
            return False, None, "Agent Environments feature is not enabled for your subscription"
        
        report_progress("Validation", 10, "Checking environment limits...")
        max_environments = self.settings.get('max_environments', 0)
        
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            self._set_tenant_context(cursor)

            self.logger.info(f"Checking for duplicate name {name}")
            
            report_progress("Database", 15, "Checking for duplicate names...")
            
            # Check for duplicate name
            cursor.execute(
                "SELECT COUNT(*) as count FROM AgentEnvironments WHERE name = ? AND is_deleted = 0",
                name
            )
            if cursor.fetchone().count > 0:
                return False, None, f"Environment '{name}' already exists"
            
            self.logger.info(f"Checking limits...")
            
            report_progress("Database", 20, "Verifying environment count...")
            
            # Check limit if not unlimited
            if max_environments != -1:
                cursor.execute("""
                    SELECT COUNT(*) as count
                    FROM AgentEnvironments
                    WHERE is_deleted = 0
                """)
                
                current_count = cursor.fetchone().count
                
                if current_count >= max_environments:
                    tier_name = self.settings.get('tier_display', 'current')
                    report_progress("Database", 100, f"Environment limit reached ({max_environments} for {tier_name} tier).")
                    return False, None, (
                        f"Environment limit reached ({max_environments} for {tier_name} tier). "
                        f"Please upgrade your subscription to create more environments."
                    )
            
            report_progress("Environment", 25, "Generating environment ID...")
            env_id = self._generate_env_id(name)

            self.logger.info(f"Generated env id {env_id}")

            self.logger.info(f"Inserting agent environment...")
            
            report_progress("Database", 30, "Creating environment record...")
            
            # Insert environment record
            cursor.execute("""
                INSERT INTO AgentEnvironments 
                (environment_id, name, description, python_version, created_by, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
            """, env_id, name, description, 
                python_version or f"{sys.version_info.major}.{sys.version_info.minor}",
                created_by)
            
            # Log usage for billing
            self._log_usage(created_by, env_id, 'create', None, cursor)
            
            conn.commit()

            self.logger.info(f"Created environment record: {env_id}")
            
            report_progress("Virtual Environment", 40, "Setting up Python environment...")

            ####################################################################
            # _ensure_virtual_environment
            env_path = os.path.join(self.base_path, env_id)
        
            if os.path.exists(env_path):
                return True
            
            try:
                self.logger.info(f"Creating virtual environment: {env_id}")
                
                python_exe = self._get_base_python_executable()
                self.logger.info(f"Using Python executable: {python_exe}")
                
                report_progress("Virtual Environment", 45, "Creating the virtual environment...")
                # Create isolated venv (NO --system-site-packages for hybrid approach)
                cmd = [python_exe, '-m', 'venv', env_path, '--clear']
                self.logger.info(f"Executing command: {cmd}")
                result = subprocess.run(cmd, capture_output=True, text=True)
                
                if result.returncode != 0:
                    self.logger.error(f"venv creation failed: {result.stderr}")
                    
                    # Try alternative method if venv module fails
                    if 'No module named venv' in result.stderr or 'ensurepip' in result.stderr:
                        self.logger.info("Standard venv failed, trying alternative method...")
                        if self._create_venv_manual(env_path, python_exe):
                            self.logger.info("Successfully created venv using manual method")
                        else:
                            raise Exception("Failed to create venv with manual method")
                    else:
                        raise Exception(f"Failed to create venv: {result.stderr}")

                # Verify the environment was created properly
                if os.name == 'nt':
                    python_check = os.path.join(env_path, 'Scripts', 'python.exe')
                    pip_check = os.path.join(env_path, 'Scripts', 'pip.exe')
                else:
                    python_check = os.path.join(env_path, 'bin', 'python')
                    pip_check = os.path.join(env_path, 'bin', 'pip')
                
                if not os.path.exists(python_check):
                    report_progress("Error", 55, f"Virtual environment creation failed - python executable not found")
                    raise Exception(f"Virtual environment creation failed - python executable not found")
                
                # Ensure pip is available
                if not os.path.exists(pip_check):
                    report_progress("Virtual Environment", 70, "pip not found, bootstrapping...")
                    self.logger.warning("pip not found, bootstrapping...")
                    self._bootstrap_pip(python_check)
                
                # Copy bundled packages to venv (for frozen builds with hybrid bundling)
                report_progress("Virtual Environment", 75, "Adding bundled packages to environment...")
                self.logger.info("Adding bundled packages to environment...")
                self._copy_bundled_packages_to_venv(env_id)
                
                # Install base packages (pip, setuptools, wheel)
                if cfg.PKG_MGR_INSTALL_BASE:
                    report_progress("Virtual Environment", 80, "Installing base packages...")
                    self.logger.info("Installing base packages...")
                    self._install_base_packages(env_id)
                
                # Install from bundle's requirements.txt if it exists (hybrid bundling)
                if self.bundle_requirements_path and os.path.exists(self.bundle_requirements_path):
                    report_progress("Virtual Environment", 85, "Installing from bundle requirements...")
                    self.logger.info(f"Installing from bundle requirements: {self.bundle_requirements_path}")
                    #self._install_from_bundle_requirements(env_id)

                    ######################################
                    # _install_from_bundle_requirements
                    pip_path = self._get_pip_path(env_id)
        
                    if not os.path.exists(pip_path):
                        self.logger.error(f"pip not found at {pip_path}")
                        return
                    
                    # Read requirements file
                    with open(self.bundle_requirements_path, 'r') as f:
                        requirements = [line.strip() for line in f.readlines() 
                                    if line.strip() and not line.startswith('#')]
                    
                    self.logger.info(f"Installing {len(requirements)} packages from requirements.txt")
                    report_progress("Virtual Environment", 86, f"Installing {len(requirements)} packages from requirements...")

                    #################################################################################
                    # Count packages live
                    import threading
                    stop_counting = threading.Event()
                    
                    def count_packages_periodically():
                        while not stop_counting.is_set():
                            pkg_count = count_packages()
                            if pkg_count > 125:
                                report_progress("Virtual Environment", 94, f"Packages installed: {pkg_count}")
                            elif pkg_count > 100:
                                report_progress("Virtual Environment", 92, f"Packages installed: {pkg_count}")
                            elif pkg_count > 75:
                                report_progress("Virtual Environment", 91, f"Packages installed: {pkg_count}")
                            elif pkg_count > 50:
                                report_progress("Virtual Environment", 90, f"Packages installed: {pkg_count}")
                            elif pkg_count > 25:
                                report_progress("Virtual Environment", 89, f"Packages installed: {pkg_count}")
                            elif pkg_count > 0:
                                report_progress("Virtual Environment", 88, f"Packages installed: {pkg_count}")
                            time.sleep(2)  # Check every 2 seconds
                    
                    # Start counter thread
                    counter = threading.Thread(target=count_packages_periodically, daemon=True)
                    counter.start()

                    # Try to install all at once first
                    result = subprocess.run(
                        [pip_path, 'install', '-r', str(self.bundle_requirements_path), '--no-cache-dir'],
                        capture_output=True,
                        text=True,
                        timeout=int(cfg.PKG_MGR_INSTALL_TIMEOUT)
                    )

                    # Stop counter and report final
                    stop_counting.set()
                    final_count = count_packages()
                    report_progress("Virtual Environment", 95, f"Complete - Total packages: {final_count}")
                    #################################################################################
                    
                    if result.returncode != 0:
                        self.logger.warning(f"Bulk install failed: {result.stderr}")
                        self.logger.info("Trying one-by-one installation...")
                        report_progress("Virtual Environment", 95, "Bulk install failed, trying one-by-one installation...")
                        
                        failed_packages = []
                        r_index = 0
                        for req in requirements:
                            r_index += 1
                            self.logger.info(f"Installing: {req}")
                            report_progress("Virtual Environment", 97, f"Installing package {r_index} of {len(requirements)}...")
                            result = subprocess.run(
                                [pip_path, 'install', req, '--no-cache-dir'],
                                capture_output=True,
                                text=True,
                                timeout=int(cfg.PKG_MGR_INSTALL_TIMEOUT)
                            )
                            
                            if result.returncode != 0:
                                self.logger.warning(f"Failed to install {req}: {result.stderr}")
                                failed_packages.append(req)
                            else:
                                self.logger.info(f"Successfully installed {req}")
                        
                        if failed_packages:
                            self.logger.error(f"Failed packages: {', '.join(failed_packages)}")
                            report_progress("Error", 97, f"Failed packages: {', '.join(failed_packages)}")
                    else:
                        self.logger.info("Successfully installed all runtime requirements")
                        report_progress("Virtual Environment", 98, "Successfully installed all runtime requirements")
                    ######################################
        
                report_progress("Virtual Environment", 99, "Activating environment...")
                
                # Update status in database
                conn = self.get_db_connection()
                cursor = conn.cursor()
                self._set_tenant_context(cursor)
                
                cursor.execute(
                    "UPDATE AgentEnvironments SET status = 'active' WHERE environment_id = ?",
                    env_id
                )
                conn.commit()
                conn.close()
                
                report_progress("Complete", 100, f"Environment '{name}' created successfully!")

                self.logger.info(f"Environment {env_id} created successfully and marked as active")
                self.logger.info(f"Returning from environment manager True, {env_id}, Environment {name} created and activated successfully")
                return True, env_id, f"Environment '{name}' created and activated successfully"
                
            except Exception as e:
                report_progress("Error", 100, f"Failed to create virtual environment")
                self.logger.error(f"Error creating virtual environment: {e}")
                # Clean up partial environment if it exists
                if os.path.exists(env_path):
                    try:
                        shutil.rmtree(env_path)
                    except:
                        pass
                try:
                    # If venv creation fails, mark as error
                    conn = self.get_db_connection()
                    cursor = conn.cursor()
                    self._set_tenant_context(cursor)
                    cursor.execute(
                        "UPDATE AgentEnvironments SET status = 'error' WHERE environment_id = ?",
                        env_id
                    )
                    conn.commit()
                    conn.close()
                except:
                    pass
                return False, env_id, f"Environment record created but activation failed"
            ####################################################################

        except Exception as e:
            self.logger.error(f"Error creating environment: {e}")
            return False, None, str(e)
        finally:
            if 'conn' in locals():
                try:
                    conn.close()
                except:
                    pass
