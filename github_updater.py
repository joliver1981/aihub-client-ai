"""
GitHub Releases Auto-Updater (MVP)
==================================

Minimal implementation for checking and downloading updates from GitHub Releases.
No external dependencies beyond Python standard library.

Setup:
    1. Add GITHUB_REPO to your app_config.py
    2. Import and register routes in app.py
    3. Add JavaScript to your base template

Usage:
    from github_updater import register_updater_routes
    register_updater_routes(app)
"""

import os
import sys
import json
import tempfile
import threading
import subprocess
import logging
import urllib.request
import urllib.error
import ssl
from functools import wraps
from typing import Optional, Dict, Any, Tuple

from logging.handlers import WatchedFileHandler
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from CommonUtils import rotate_logs_on_startup


# ============================================================================
# LOGGING
# ============================================================================
def setup_logging():
    """Configure logging with dedicated log file"""
    _logger = logging.getLogger("GitHubUpdater")
    log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
    log_level = getattr(logging, log_level_name, logging.DEBUG)
    _logger.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler = WatchedFileHandler(filename=os.getenv('GITHUB_UPDATER_LOG', './logs/github_updater_log.txt'), encoding='utf-8')
    handler.setFormatter(formatter)
    _logger.addHandler(handler)
    return _logger

rotate_logs_on_startup(os.getenv('GITHUB_UPDATER_LOG', './logs/github_updater_log.txt'))
logger = setup_logging()


# ============================================================================
# DECORATORS & HELPERS
# ============================================================================
def admin_required(f):
    """Decorator to ensure only admins can access"""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not hasattr(current_user, 'role') or current_user.role < 3:
            return "Access denied", 403
        return f(*args, **kwargs)
    return decorated_function


# ============================================================================
# SSL CONTEXT FOR PYINSTALLER BUNDLES
# ============================================================================
def _get_ssl_context():
    """
    Get an SSL context that works in PyInstaller bundles.

    PyInstaller-bundled apps often can't find system certificates.
    We try certifi first, then fall back to system defaults.
    """
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
        logger.debug(f"Using certifi certificates from: {certifi.where()}")
        return context
    except ImportError:
        logger.debug("certifi not available, using default SSL context")
        pass

    # Try creating a context with system certificates
    try:
        context = ssl.create_default_context()
        return context
    except Exception as e:
        logger.warning(f"Could not create SSL context: {e}")
        return None

# ============================================================================
# CONFIGURATION
# ============================================================================
#
# Environment variables (override config file - useful for testing):
#   AIHUB_GITHUB_REPO   - GitHub repo for releases (e.g., "everiai-aihub/releases-test")
#   AIHUB_VERSION       - Override current version (e.g., "1.0.0" to simulate old version)
#
# Example usage for testing:
#   Windows:  set AIHUB_GITHUB_REPO=everiai-aihub/releases-test
#   Linux:    export AIHUB_GITHUB_REPO=everiai-aihub/releases-test
#
# ============================================================================

# Import from your app_config, or set defaults
try:
    import app_config
    _config_version = getattr(app_config, 'APP_VERSION', '1.0.0')
    _config_repo = getattr(app_config, 'GITHUB_REPO', 'everiai-aihub/releases')
    _config_app_name = getattr(app_config, 'APP_NAME', 'AIHub')
except ImportError:
    _config_version = '1.0.0'
    _config_repo = 'everiai-aihub/releases'
    _config_app_name = 'AIHub'

# Environment variables override config file
CURRENT_VERSION = os.environ.get('AIHUB_VERSION', _config_version)
GITHUB_REPO = os.environ.get('AIHUB_GITHUB_REPO', _config_repo)
APP_NAME = _config_app_name

# GitHub API endpoint
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# ============================================================================
# DOWNLOAD FOLDER CONFIGURATION
# ============================================================================
# Prefer APP_ROOT/updates (works when running as service via NSSM).
# Fall back to ProgramData, then system temp as last resort.
# ============================================================================

def _get_download_folder() -> str:
    """
    Get download folder, preferring APP_ROOT env variable.
    Falls back to ProgramData then temp directory if APP_ROOT is not set or not writable.
    """
    # Option 1: APP_ROOT/updates (best for NSSM services)
    app_root = os.environ.get('APP_ROOT')
    if app_root:
        folder = os.path.join(app_root, 'updates')
        try:
            os.makedirs(folder, exist_ok=True)
            # Test write access
            test_file = os.path.join(folder, '.write_test')
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
            logger.info(f"Using APP_ROOT download folder: {folder}")
            return folder
        except (OSError, PermissionError) as e:
            logger.warning(f"Cannot use APP_ROOT folder {folder}: {e}")

    # Option 2: ProgramData (writable by LocalSystem)
    if sys.platform == 'win32':
        program_data = os.environ.get('ProgramData', r'C:\ProgramData')
        folder = os.path.join(program_data, APP_NAME, 'updates')
        try:
            os.makedirs(folder, exist_ok=True)
            logger.info(f"Using ProgramData download folder: {folder}")
            return folder
        except (OSError, PermissionError) as e:
            logger.warning(f"ProgramData folder not writable ({e}), falling back to temp")

    # Option 3: Temp directory (last resort)
    folder = os.path.join(tempfile.gettempdir(), APP_NAME, 'updates')
    os.makedirs(folder, exist_ok=True)
    logger.info(f"Using temp download folder: {folder}")
    return folder


DOWNLOAD_FOLDER = _get_download_folder()

# Log configuration on import (helpful for debugging)
logger.info(f"Updater configured: version={CURRENT_VERSION}, repo={GITHUB_REPO}, download_folder={DOWNLOAD_FOLDER}")


# ============================================================================
# VERSION COMPARISON
# ============================================================================

def parse_version(version_str: str) -> Tuple[int, ...]:
    """Parse version string like '1.2.3' or 'v1.2.3' into tuple (1, 2, 3)"""
    # Remove 'v' prefix if present
    version_str = version_str.lstrip('vV')

    # Remove pre-release suffix (e.g., '-beta.1')
    version_str = version_str.split('-')[0]

    parts = []
    for part in version_str.split('.'):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)

    # Ensure at least 3 parts
    while len(parts) < 3:
        parts.append(0)

    return tuple(parts)


def is_newer_version(latest: str, current: str) -> bool:
    """Check if latest version is newer than current"""
    return parse_version(latest) > parse_version(current)


# ============================================================================
# GITHUB API FUNCTIONS
# ============================================================================

def check_github_for_update() -> Dict[str, Any]:
    """
    Check GitHub Releases API for updates.

    Returns:
        Dict with update info or error
    """
    try:
        logger.info(f"Checking for updates at: {GITHUB_API_URL}")

        # Create request with User-Agent (required by GitHub)
        request = urllib.request.Request(
            GITHUB_API_URL,
            headers={
                'User-Agent': f'{APP_NAME}-Updater/1.0',
                'Accept': 'application/vnd.github.v3+json'
            }
        )

        # Get SSL context for PyInstaller compatibility
        ssl_context = _get_ssl_context()

        # Make request with SSL context
        with urllib.request.urlopen(request, timeout=15, context=ssl_context) as response:
            data = json.loads(response.read().decode('utf-8'))

        # Parse response
        latest_version = data.get('tag_name', '').lstrip('vV')

        if not latest_version:
            return {
                'update_available': False,
                'error': 'Could not determine latest version'
            }

        # Find the .exe asset
        exe_asset = None
        for asset in data.get('assets', []):
            if asset['name'].lower().endswith('.exe'):
                exe_asset = asset
                break

        if not exe_asset:
            return {
                'update_available': False,
                'error': 'No installer found in release'
            }

        # Compare versions
        if is_newer_version(latest_version, CURRENT_VERSION):
            return {
                'update_available': True,
                'current_version': CURRENT_VERSION,
                'latest_version': latest_version,
                'download_url': exe_asset['browser_download_url'],
                'file_name': exe_asset['name'],
                'file_size': exe_asset['size'],
                'release_notes': data.get('body', ''),
                'published_at': data.get('published_at', '')
            }
        else:
            return {
                'update_available': False,
                'current_version': CURRENT_VERSION,
                'latest_version': latest_version,
                'message': 'You are running the latest version'
            }

    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {
                'update_available': False,
                'error': 'No releases found. Create your first release on GitHub.'
            }
        logger.error(f"GitHub API error: {e}")
        return {
            'update_available': False,
            'error': f'GitHub API error: {e.code}'
        }

    except urllib.error.URLError as e:
        logger.error(f"Network error checking for updates: {e}")
        return {
            'update_available': False,
            'error': 'Network error. Please check your internet connection.'
        }

    except Exception as e:
        logger.error(f"Update check failed: {e}")
        return {
            'update_available': False,
            'error': str(e)
        }


# ============================================================================
# DOWNLOAD FUNCTIONS
# ============================================================================

# Global download state
_download_state = {
    'in_progress': False,
    'progress': 0.0,
    'error': None,
    'file_path': None,
    'version': None
}


def download_update(download_url: str, file_name: str, version: str) -> Dict[str, Any]:
    """
    Download update installer to download folder.

    Args:
        download_url: GitHub asset download URL
        file_name: Name of the installer file
        version: Version being downloaded

    Returns:
        Dict with download result
    """
    global _download_state

    try:
        # Create download folder
        os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

        # Full path for downloaded file
        file_path = os.path.join(DOWNLOAD_FOLDER, file_name)

        # Delete existing file if present (prevents permission denied on overwrite)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"Removed existing file: {file_path}")
            except OSError as e:
                logger.warning(f"Could not remove existing file {file_path}: {e}")
                # Try with a unique filename as fallback
                import time
                base, ext = os.path.splitext(file_name)
                file_name = f"{base}_{int(time.time())}{ext}"
                file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
                logger.info(f"Using alternative filename: {file_path}")

        # Update state
        _download_state = {
            'in_progress': True,
            'progress': 0.0,
            'error': None,
            'file_path': None,
            'version': version
        }

        logger.info(f"Downloading {file_name} to {file_path}")

        # Create request
        request = urllib.request.Request(
            download_url,
            headers={'User-Agent': f'{APP_NAME}-Updater/1.0'}
        )

        # Get SSL context for PyInstaller compatibility
        ssl_context = _get_ssl_context()

        # Download with progress tracking
        with urllib.request.urlopen(request, timeout=300, context=ssl_context) as response:
            total_size = int(response.headers.get('Content-Length', 0))
            downloaded = 0

            with open(file_path, 'wb') as f:
                while True:
                    chunk = response.read(65536)  # 64KB chunks
                    if not chunk:
                        break

                    f.write(chunk)
                    downloaded += len(chunk)

                    if total_size > 0:
                        _download_state['progress'] = downloaded / total_size

        # Download complete
        _download_state['in_progress'] = False
        _download_state['progress'] = 1.0
        _download_state['file_path'] = file_path

        logger.info(f"Download complete: {file_path} ({downloaded} bytes)")

        return {
            'success': True,
            'file_path': file_path,
            'version': version
        }

    except Exception as e:
        logger.error(f"Download failed: {e}")
        _download_state['in_progress'] = False
        _download_state['error'] = str(e)

        return {
            'success': False,
            'error': str(e)
        }


def download_update_async(download_url: str, file_name: str, version: str):
    """Start download in background thread"""
    thread = threading.Thread(
        target=download_update,
        args=(download_url, file_name, version),
        daemon=True
    )
    thread.start()


# ============================================================================
# SESSION 0 DETECTION
# ============================================================================

def _is_running_in_session_0() -> bool:
    """
    Detect if the process is running in Session 0 (Windows service session).

    Session 0 is non-interactive: no desktop, no UAC prompts, no visible windows.
    When running as LocalSystem via NSSM, the process will be in Session 0.
    """
    if sys.platform != 'win32':
        return False

    try:
        import ctypes
        from ctypes import wintypes

        process_id = ctypes.windll.kernel32.GetCurrentProcessId()
        session_id = wintypes.DWORD()

        if ctypes.windll.kernel32.ProcessIdToSessionId(process_id, ctypes.byref(session_id)):
            is_session_0 = session_id.value == 0
            logger.info(f"Process session ID: {session_id.value} (session_0={is_session_0})")
            return is_session_0

    except Exception as e:
        logger.warning(f"Could not determine session ID: {e}")

    # Fallback: check if parent process is a service manager
    try:
        import psutil
        parent = psutil.Process(os.getpid()).parent()
        if parent and parent.name().lower() in ('services.exe', 'nssm.exe', 'srvany.exe'):
            logger.info(f"Parent process is {parent.name()} - likely running as service")
            return True
    except Exception:
        pass

    return False


# ============================================================================
# INSTALLATION FUNCTIONS
# ============================================================================

# Global install state - tracks background silent installs
_install_state = {
    'in_progress': False,
    'status': None,     # 'running', 'success', 'failed'
    'message': None,
    'method': None,     # 'silent_service', 'silent_interactive', 'interactive'
    'exit_code': None
}


def launch_installer(file_path: str, silent: bool = False) -> Dict[str, Any]:
    """
    Launch the downloaded installer with Session 0 awareness.

    Handles two scenarios:

    1. Running as a Windows service in Session 0 (e.g. LocalSystem via NSSM):
       Uses /VERYSILENT /SUPPRESSMSGBOXES /SP- so the installer runs completely
       headless. No UI, no UAC prompt needed (LocalSystem is already admin),
       no desktop required. The Inno Setup script's own StopAndRemoveServices()
       and InstallServices() handle the full upgrade lifecycle.

    2. Running in an interactive session (user ran app directly):
       Uses ShellExecute for proper UAC elevation and installer UI.

    Args:
        file_path: Path to the Inno Setup installer .exe
        silent: If True, run with /VERYSILENT (no UI at all).
                Automatically forced to True when in Session 0.

    Returns:
        Dict with launch result including method used
    """
    global _install_state

    try:
        if not os.path.exists(file_path):
            return {
                'success': False,
                'error': 'Installer file not found',
                'download_folder': DOWNLOAD_FOLDER
            }

        # Inno Setup command line reference:
        # /SP-              - Suppress "This will install..." initial prompt
        # /SILENT           - Shows progress bar but no prompts
        # /VERYSILENT       - No UI at all (completely headless)
        # /SUPPRESSMSGBOXES - Auto-accept all MsgBox calls in [Code] section
        # /CLOSEAPPLICATIONS    - Close running instances via restart manager
        # /RESTARTAPPLICATIONS  - Restart apps after install
        # /NORESTART        - Don't reboot Windows

        is_service = sys.platform == 'win32' and _is_running_in_session_0()

        # Force silent when in Session 0 - there's no desktop to show UI on,
        # and LocalSystem already has admin rights so UAC is not needed.
        if is_service:
            silent = True
            method = 'silent_service'
            logger.info("Session 0 detected - forcing silent install (no desktop available)")
        elif silent:
            method = 'silent_interactive'
            logger.info("Silent install requested from interactive session")
        else:
            method = 'interactive'
            logger.info("Interactive install from user session")

        # Build Inno Setup command line arguments
        args = [file_path]

        if silent:
            # Session 0 (service) or explicit silent: fully silent, no desktop interaction.
            # /SUPPRESSMSGBOXES is critical - the Inno script has MsgBox calls
            # in GetInstalledVersion(), CurStepChanged(), and EnsureEnvKeyExists()
            # that would hang forever in Session 0 without it.
            # LocalSystem already has admin rights so no UAC is needed.
            args.extend([
                '/VERYSILENT',
                '/SUPPRESSMSGBOXES',
                '/SP-',
                '/NORESTART',
            ])
            # NOTE: We intentionally do NOT pass /CLOSEAPPLICATIONS or
            # /RESTARTAPPLICATIONS here. The .iss script sets CloseApplications=no
            # and handles service lifecycle itself via StopAndRemoveServices()
            # and InstallServices(). Passing /CLOSEAPPLICATIONS on the command
            # line would override that setting and enable the Windows Restart
            # Manager, which hangs in Session 0 waiting for cross-session
            # WM_CLOSE responses that never arrive (0% CPU deadlock).

            # Add a log file for debugging silent installs
            log_dir = os.path.dirname(file_path)
            log_path = os.path.join(log_dir, 'install.log')
            args.append(f'/LOG={log_path}')

            logger.info(f"Silent install command: {' '.join(args)}")
            logger.info(f"Install log will be at: {log_path}")

            # Run in background thread so we can track completion
            # and report status back to the frontend.
            _install_state.update({
                'in_progress': True,
                'status': 'running',
                'message': 'Silent install in progress...',
                'method': method,
                'exit_code': None
            })

            def _run_silent_install():
                """Background thread: run installer and track result."""
                try:
                    logger.info("Starting silent installer process...")

                    # Inno Setup is a GUI application — it doesn't write to
                    # stdout/stderr, so we redirect to DEVNULL rather than
                    # PIPE to avoid any potential pipe-related issues.
                    # Debug output goes to the /LOG= file instead.
                    proc = subprocess.Popen(
                        args,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        stdin=subprocess.DEVNULL,
                        creationflags=(
                            subprocess.CREATE_NEW_PROCESS_GROUP
                        ) if sys.platform == 'win32' else 0
                    )

                    # Wait for installer to finish (timeout after 5 minutes)
                    try:
                        exit_code = proc.wait(timeout=300)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                        exit_code = -1
                        logger.error("Silent installer timed out after 5 minutes")

                    if exit_code == 0:
                        logger.info("Silent install completed successfully (exit code 0)")
                        _install_state.update({
                            'in_progress': False,
                            'status': 'success',
                            'message': 'Update installed successfully. Application is restarting...',
                            'exit_code': exit_code
                        })
                    else:
                        error_msg = f"Installer exited with code {exit_code}. Check install.log in the updates folder for details."
                        logger.error(error_msg)
                        _install_state.update({
                            'in_progress': False,
                            'status': 'failed',
                            'message': error_msg,
                            'exit_code': exit_code
                        })

                except Exception as e:
                    logger.error(f"Silent install thread error: {e}")
                    _install_state.update({
                        'in_progress': False,
                        'status': 'failed',
                        'message': str(e),
                        'exit_code': -1
                    })

            install_thread = threading.Thread(
                target=_run_silent_install,
                daemon=True,
                name='silent-installer'
            )
            install_thread.start()

            return {
                'success': True,
                'method': method,
                'message': 'Silent install started. The application will restart automatically.',
                'session_zero': is_service,
                'installer_path': file_path
            }

        elif sys.platform == 'win32':
            # Interactive session - use ShellExecute for proper UAC elevation
            import ctypes

            shell_args = '/CLOSEAPPLICATIONS /RESTARTAPPLICATIONS'

            logger.info(f"Interactive session - launching with ShellExecute: {file_path} {shell_args}")

            result = ctypes.windll.shell32.ShellExecuteW(
                None,
                "runas",        # Request elevation (UAC prompt)
                file_path,
                shell_args,
                None,
                1               # SW_SHOWNORMAL
            )

            if result > 32:
                logger.info(f"ShellExecute launch successful (result={result})")
                return {
                    'success': True,
                    'message': 'Installer launched. The application will restart automatically.',
                    'method': 'interactive',
                    'installer_path': file_path
                }
            else:
                error_msg = f'ShellExecute returned {result}'
                logger.error(error_msg)
                return {
                    'success': False,
                    'method': 'interactive',
                    'error': error_msg,
                    'installer_path': file_path,
                    'download_folder': DOWNLOAD_FOLDER
                }

        else:
            # Non-Windows: direct launch
            subprocess.Popen([file_path, '/CLOSEAPPLICATIONS', '/RESTARTAPPLICATIONS'])
            return {
                'success': True,
                'message': 'Installer launched.',
                'method': 'subprocess',
                'installer_path': file_path
            }

    except Exception as e:
        logger.error(f"Failed to launch installer: {e}")
        return {
            'success': False,
            'error': str(e),
            'download_folder': DOWNLOAD_FOLDER
        }


# ============================================================================
# FLASK ROUTES
# ============================================================================

updater_bp = Blueprint('updater', __name__, url_prefix='/api/updater')


@updater_bp.route('/check', methods=['GET'])
@admin_required
def api_check_update():
    """Check for available updates"""
    try:
        result = check_github_for_update()
        return jsonify({
            'status': 'success',
            **result
        })

    except Exception as e:
        logger.error(f"Update check API error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@updater_bp.route('/download', methods=['POST'])
@admin_required
def api_download_update():
    """Start downloading an update"""
    try:
        global _download_state

        # Check if already downloading
        if _download_state.get('in_progress'):
            return jsonify({
                'status': 'error',
                'message': 'Download already in progress'
            }), 400

        # Get download info from request or check again
        data = request.get_json() or {}

        if 'download_url' in data:
            download_url = data['download_url']
            file_name = data.get('file_name', 'AIHub_Setup.exe')
            version = data.get('version', 'unknown')
        else:
            # Check for update to get download URL
            update_info = check_github_for_update()
            if not update_info.get('update_available'):
                return jsonify({
                    'status': 'error',
                    'message': 'No update available'
                }), 400

            download_url = update_info['download_url']
            file_name = update_info['file_name']
            version = update_info['latest_version']

        # Start async download
        download_update_async(download_url, file_name, version)

        return jsonify({
            'status': 'success',
            'message': 'Download started',
            'version': version
        })

    except Exception as e:
        logger.error(f"Download API error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@updater_bp.route('/download/progress', methods=['GET'])
@admin_required
def api_download_progress():
    """Get current download progress"""
    return jsonify({
        'status': 'success',
        'in_progress': _download_state.get('in_progress', False),
        'progress': _download_state.get('progress', 0),
        'error': _download_state.get('error'),
        'complete': _download_state.get('progress', 0) >= 1.0 and not _download_state.get('in_progress', False)
    })


@updater_bp.route('/install', methods=['POST'])
@admin_required
def api_install_update():
    """Launch the downloaded installer"""
    try:
        file_path = _download_state.get('file_path')

        if not file_path or not os.path.exists(file_path):
            return jsonify({
                'status': 'error',
                'message': 'No downloaded update found. Please download first.',
                'download_folder': DOWNLOAD_FOLDER
            }), 400

        data = request.get_json() or {}
        silent = data.get('silent', True)  # Default to silent (production behavior)

        result = launch_installer(file_path, silent=silent)

        if result.get('success'):
            return jsonify({
                'status': 'success',
                'message': result['message'],
                'method': result.get('method', 'unknown'),
                'installer_path': file_path
            })
        else:
            return jsonify({
                'status': 'error',
                'message': result.get('error', 'Installation failed'),
                'installer_path': file_path,
                'download_folder': result.get('download_folder', DOWNLOAD_FOLDER)
            }), 500

    except Exception as e:
        logger.error(f"Install API error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@updater_bp.route('/install/status', methods=['GET'])
@admin_required
def api_install_status():
    """
    Get silent install progress.

    The frontend polls this after triggering a silent install to know
    when the installer has finished (and the app is about to restart).
    """
    return jsonify({
        'status': 'success',
        'install': {
            'in_progress': _install_state.get('in_progress', False),
            'status': _install_state.get('status'),
            'message': _install_state.get('message'),
            'method': _install_state.get('method'),
            'exit_code': _install_state.get('exit_code')
        }
    })


@updater_bp.route('/open-folder', methods=['POST'])
@admin_required
def api_open_installer_folder():
    """Open the folder containing the downloaded installer in Explorer"""
    try:
        file_path = _download_state.get('file_path')

        if not file_path:
            return jsonify({
                'status': 'error',
                'message': 'No installer file path available.'
            }), 400

        folder_path = os.path.dirname(file_path)

        if not os.path.exists(folder_path):
            return jsonify({
                'status': 'error',
                'message': f'Folder not found: {folder_path}'
            }), 404

        if sys.platform == 'win32':
            # Open Explorer and select the file if it exists, otherwise just open the folder
            if os.path.exists(file_path):
                subprocess.Popen(['explorer.exe', '/select,', file_path])
            else:
                subprocess.Popen(['explorer.exe', folder_path])

        return jsonify({
            'status': 'success',
            'message': 'Folder opened.',
            'folder_path': folder_path,
            'file_name': os.path.basename(file_path)
        })

    except Exception as e:
        logger.error(f"Open folder error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@updater_bp.route('/version', methods=['GET'])
@admin_required
def api_get_version():
    """Get current app version and update configuration"""
    return jsonify({
        'status': 'success',
        'version': CURRENT_VERSION,
        'app_name': APP_NAME,
        'github_repo': GITHUB_REPO,
        'update_check_url': GITHUB_API_URL
    })


# ============================================================================
# REGISTRATION
# ============================================================================

def register_updater_routes(app):
    """
    Register updater routes with Flask app.

    Usage in app.py:
        from github_updater import register_updater_routes
        register_updater_routes(app)
    """
    app.register_blueprint(updater_bp)
    logger.info(f"Updater routes registered - Current version: {CURRENT_VERSION}")
