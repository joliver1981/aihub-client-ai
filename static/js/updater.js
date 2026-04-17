/**
 * AI Hub Update Banner (MVP)
 * ==========================
 *
 * Minimal JavaScript for showing update notifications and handling downloads.
 * Supports silent updates when the backend runs as a Windows service (Session 0).
 *
 * Usage:
 *   1. Include this script in your base.html
 *   2. It auto-initializes on page load
 *   3. Shows banner when update is available
 *
 * Manual control:
 *   AIHubUpdater.check()           - Force check for updates
 *   AIHubUpdater.download()        - Start download
 *   AIHubUpdater.install()         - Launch installer (silent)
 *   AIHubUpdater.dismiss()         - Hide banner
 */

const AIHubUpdater = (function() {
    'use strict';

    // State
    let updateInfo = null;
    let isDownloading = false;
    let downloadComplete = false;
    let installerPath = null;

    // Configuration
    const config = {
        checkOnLoad: true,
        checkDelayMs: 5000,        // Wait 5s after page load before checking
        progressPollMs: 500,       // Poll download progress every 500ms
        installPollMs: 2000,       // Poll install status every 2s
        restartPollMs: 2000,       // Poll for app restart every 2s
        restartTimeoutMs: 300000,  // Give up after 5 minutes
        apiBase: '/api/updater'
    };

    // ========================================================================
    // UI CREATION
    // ========================================================================

    function createBannerHTML() {
        return `
            <div id="update-banner" style="display: none;">
                <div class="update-banner-content">
                    <div class="update-banner-icon">&#x1F514;</div>
                    <div class="update-banner-text">
                        <strong id="update-banner-title">Update Available</strong>
                        <span id="update-banner-subtitle"></span>
                    </div>
                    <div class="update-banner-actions" id="update-banner-actions">
                        <button class="btn btn-sm btn-light" onclick="AIHubUpdater.showNotes()">What's New</button>
                        <button class="btn btn-sm btn-primary" id="update-btn-main" onclick="AIHubUpdater.download()">
                            Download Update
                        </button>
                        <button class="btn btn-sm btn-link text-white" onclick="AIHubUpdater.dismiss()" title="Dismiss">
                            &#x2715;
                        </button>
                    </div>
                    <div class="update-banner-progress" id="update-banner-progress" style="display: none;">
                        <div class="progress" style="width: 200px; height: 8px;">
                            <div class="progress-bar progress-bar-striped progress-bar-animated"
                                 id="update-progress-bar" role="progressbar" style="width: 0%"></div>
                        </div>
                        <small id="update-progress-text">Downloading... 0%</small>
                    </div>
                </div>
            </div>

            <style>
                #update-banner {
                    position: fixed;
                    top: 0;
                    left: 0;
                    right: 0;
                    z-index: 1030;  /* Below Bootstrap modal-backdrop (1040) and modal (1050) */
                    background: linear-gradient(135deg, #2471a3 0%, #2980b9 100%);
                    color: white;
                    padding: 10px 20px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.2);
                    font-family: inherit;
                }

                #update-banner.critical {
                    background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
                }

                #update-banner.installing {
                    background: linear-gradient(135deg, #059669 0%, #10b981 100%);
                }

                .update-banner-content {
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    gap: 15px;
                    max-width: 1200px;
                    margin: 0 auto;
                    flex-wrap: wrap;
                }

                .update-banner-icon {
                    font-size: 24px;
                }

                .update-banner-text {
                    display: flex;
                    align-items: center;
                    gap: 10px;
                    flex-wrap: wrap;
                }

                .update-banner-text strong {
                    font-size: 14px;
                }

                .update-banner-text span {
                    font-size: 13px;
                    opacity: 0.9;
                }

                .update-banner-actions {
                    display: flex;
                    gap: 8px;
                    align-items: center;
                }

                .update-banner-actions .btn-light {
                    background: rgba(255,255,255,0.2);
                    border-color: rgba(255,255,255,0.3);
                    color: white;
                }

                .update-banner-actions .btn-light:hover {
                    background: rgba(255,255,255,0.3);
                }

                .update-banner-actions .btn-primary {
                    background: white;
                    border-color: white;
                    color: #2471a3;
                }

                .update-banner-actions .btn-primary:hover {
                    background: #f0f0f0;
                }

                .update-banner-progress {
                    display: flex;
                    align-items: center;
                    gap: 10px;
                }

                .update-banner-progress .progress {
                    background: rgba(255,255,255,0.3);
                }

                .update-banner-progress .progress-bar {
                    background: white;
                }

                /* Push page content down when banner is visible */
                body.has-update-banner {
                    padding-top: 50px;
                }

                /* Spinner for install phase */
                .update-spinner {
                    display: inline-block;
                    width: 16px;
                    height: 16px;
                    border: 2px solid rgba(255,255,255,0.3);
                    border-radius: 50%;
                    border-top-color: white;
                    animation: spin 0.8s linear infinite;
                    margin-right: 8px;
                    vertical-align: middle;
                }

                @keyframes spin {
                    to { transform: rotate(360deg); }
                }

                /* Toast animations */
                @keyframes slideIn {
                    from {
                        transform: translateX(100px);
                        opacity: 0;
                    }
                    to {
                        transform: translateX(0);
                        opacity: 1;
                    }
                }

                @keyframes fadeOut {
                    from { opacity: 1; }
                    to { opacity: 0; }
                }
            </style>
        `;
    }

    function createNotesModalHTML() {
        return `
            <style>
                /* Ensure update notes modal appears above the update banner */
                #updateNotesModal {
                    z-index: 1060 !important;
                }
                /* Only target backdrop when update notes modal is open - use sibling selector */
                #updateNotesModal.show ~ .modal-backdrop:last-of-type {
                    z-index: 1055 !important;
                }
            </style>
            <div class="modal fade" id="updateNotesModal" tabindex="-1">
                <div class="modal-dialog">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">
                                <span id="notes-modal-version"></span> - What's New
                            </h5>
                            <button type="button" class="close" data-dismiss="modal">
                                <span>&times;</span>
                            </button>
                        </div>
                        <div class="modal-body">
                            <div id="notes-modal-content"></div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-dismiss="modal">Later</button>
                            <button type="button" class="btn btn-primary" onclick="AIHubUpdater.download(); $('#updateNotesModal').modal('hide');">
                                Download Now
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    function injectUI() {
        // Only inject once
        if (document.getElementById('update-banner')) return;

        // Create container
        const container = document.createElement('div');
        container.id = 'aihub-updater-container';
        container.innerHTML = createBannerHTML() + createNotesModalHTML();
        document.body.appendChild(container);
    }

    // ========================================================================
    // API CALLS
    // ========================================================================

    async function checkForUpdate(force = false) {
        try {
            // If force check, clear any dismissal
            if (force) {
                sessionStorage.removeItem('update_dismissed');
            }

            const response = await fetch(`${config.apiBase}/check`);
            const data = await response.json();

            if (data.status === 'success' && data.update_available) {
                updateInfo = {
                    currentVersion: data.current_version,
                    latestVersion: data.latest_version,
                    downloadUrl: data.download_url,
                    fileName: data.file_name,
                    fileSize: data.file_size,
                    releaseNotes: data.release_notes || 'No release notes available.',
                    publishedAt: data.published_at
                };

                showBanner();
                return updateInfo;
            } else if (force) {
                // If forced check and no update, show notification
                showNoUpdateMessage(data.current_version);
            }

            return null;
        } catch (error) {
            console.error('Update check failed:', error);
            if (force) {
                showCheckError();
            }
            return null;
        }
    }

    function showNoUpdateMessage(currentVersion) {
        // Create a temporary toast notification
        const toast = document.createElement('div');
        toast.className = 'update-toast';
        toast.innerHTML = `
            <div class="update-toast-content">
                <i class="fas fa-check-circle text-success mr-2"></i>
                <span><strong>You're up to date!</strong> Running version ${currentVersion}</span>
            </div>
        `;
        toast.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 1060;
            background: white;
            padding: 15px 20px;
            border-radius: 8px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.15);
            animation: slideIn 0.3s ease;
        `;

        document.body.appendChild(toast);

        // Remove after 4 seconds
        setTimeout(() => {
            toast.style.animation = 'fadeOut 0.3s ease';
            setTimeout(() => toast.remove(), 300);
        }, 4000);
    }

    function showCheckError() {
        const toast = document.createElement('div');
        toast.innerHTML = `
            <div style="display: flex; align-items: center; gap: 10px;">
                <i class="fas fa-exclamation-circle text-warning"></i>
                <span>Could not check for updates. Please try again later.</span>
            </div>
        `;
        toast.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 1060;
            background: white;
            padding: 15px 20px;
            border-radius: 8px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.15);
        `;

        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 4000);
    }

    async function startDownload() {
        if (isDownloading || !updateInfo) return;

        isDownloading = true;
        downloadComplete = false;

        // Update UI
        document.getElementById('update-banner-actions').style.display = 'none';
        document.getElementById('update-banner-progress').style.display = 'flex';
        document.getElementById('update-banner-title').textContent = 'Downloading Update...';

        try {
            const response = await fetch(`${config.apiBase}/download`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    download_url: updateInfo.downloadUrl,
                    file_name: updateInfo.fileName,
                    version: updateInfo.latestVersion
                })
            });

            const data = await response.json();

            if (data.status === 'success') {
                pollDownloadProgress();
            } else {
                throw new Error(data.message || 'Download failed');
            }
        } catch (error) {
            console.error('Download failed:', error);
            isDownloading = false;
            showDownloadFailedWithManualOption(error.message);
        }
    }

    function showDownloadFailedWithManualOption(errorMessage) {
        document.getElementById('update-banner-progress').style.display = 'none';
        document.getElementById('update-banner-title').textContent = '\u26A0\uFE0F Download Issue';
        document.getElementById('update-banner-subtitle').textContent = '';

        const actionsDiv = document.getElementById('update-banner-actions');
        actionsDiv.innerHTML = `
            <div style="text-align: center; width: 100%;">
                <p style="margin: 0 0 10px 0;">
                    The automatic download couldn't complete. This may happen if the service account doesn't have internet access.
                </p>
                <p style="margin: 0 0 15px 0;">
                    You can download the update manually using your browser:
                </p>
                <div style="display: flex; gap: 10px; justify-content: center; flex-wrap: wrap;">
                    <a href="${updateInfo.downloadUrl}"
                       class="btn btn-sm btn-primary"
                       target="_blank"
                       style="background: white; border-color: white; color: #2471a3;">
                        <i class="fas fa-download mr-1"></i> Download in Browser
                    </a>
                    <button class="btn btn-sm btn-light" onclick="AIHubUpdater.download()">
                        <i class="fas fa-redo mr-1"></i> Retry
                    </button>
                    <button class="btn btn-sm btn-link text-white" onclick="AIHubUpdater.dismiss()">
                        Later
                    </button>
                </div>
                <p style="margin: 10px 0 0 0; font-size: 12px; opacity: 0.8;">
                    After downloading, run the installer from your Downloads folder.
                </p>
            </div>
        `;
        actionsDiv.style.display = 'block';
    }

    async function pollDownloadProgress() {
        try {
            const response = await fetch(`${config.apiBase}/download/progress`);
            const data = await response.json();

            const percent = Math.round(data.progress * 100);
            document.getElementById('update-progress-bar').style.width = `${percent}%`;
            document.getElementById('update-progress-text').textContent = `Downloading... ${percent}%`;

            if (data.error) {
                isDownloading = false;
                showDownloadFailedWithManualOption(data.error);
                return;
            }

            if (data.complete) {
                isDownloading = false;
                downloadComplete = true;
                showInstallReady();
                return;
            }

            if (data.in_progress) {
                setTimeout(pollDownloadProgress, config.progressPollMs);
            }
        } catch (error) {
            console.error('Progress check failed:', error);
            setTimeout(pollDownloadProgress, config.progressPollMs);
        }
    }

    // ========================================================================
    // INSTALLATION (SILENT)
    // ========================================================================

    async function installUpdate() {
        if (!downloadComplete) return;

        // Transition banner to "installing" state
        const banner = document.getElementById('update-banner');
        banner.classList.add('installing');

        document.querySelector('.update-banner-icon').innerHTML = '&#x1F504;';
        document.getElementById('update-banner-title').innerHTML =
            '<span class="update-spinner"></span>Installing Update...';
        document.getElementById('update-banner-subtitle').textContent =
            'This could take 5 to 10 minutes to complete. The app will restart automatically.';
        document.getElementById('update-banner-actions').style.display = 'none';
        document.getElementById('update-banner-progress').style.display = 'none';

        try {
            // Request silent install - the backend handles Session 0 detection
            // and will run /VERYSILENT when running as a service
            const response = await fetch(`${config.apiBase}/install`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ silent: true })
            });

            const data = await response.json();

            // Store installer path for folder link fallback
            if (data.installer_path) {
                installerPath = data.installer_path;
            }

            if (data.status === 'success') {
                console.log(`Install started via method: ${data.method}`);

                // Start polling install status, then poll for restart
                pollInstallStatus();
            } else {
                throw new Error(data.message || 'Installation failed');
            }
        } catch (error) {
            console.error('Install failed:', error);
            showError('Installation failed: ' + error.message, true);
        }
    }

    async function pollInstallStatus() {
        /**
         * Poll the /install/status endpoint to track the silent installer.
         * Once the installer finishes, the NSSM service will restart the app,
         * so we transition to polling for the app to come back online.
         */
        try {
            const response = await fetch(`${config.apiBase}/install/status`);
            const data = await response.json();
            const install = data.install || {};

            if (install.status === 'success') {
                // Installer finished - app is about to restart
                showRestartingMessage();
                pollForRestart();
                return;
            }

            if (install.status === 'failed') {
                showError('Installation failed: ' + (install.message || 'Unknown error'), true);
                return;
            }

            // Still running - keep polling
            if (install.in_progress) {
                setTimeout(pollInstallStatus, config.installPollMs);
            }
        } catch (error) {
            // If we can't reach the server, the app is probably restarting
            // (the installer closed it). Switch to restart polling.
            console.log('Lost connection during install - app is likely restarting');
            showRestartingMessage();
            pollForRestart();
        }
    }

    // ========================================================================
    // RESTART POLLING
    // ========================================================================

    let restartPollStart = 0;

    function showRestartingMessage() {
        const banner = document.getElementById('update-banner');
        banner.classList.add('installing');

        document.querySelector('.update-banner-icon').innerHTML = '&#x1F504;';
        document.getElementById('update-banner-title').innerHTML =
            '<span class="update-spinner"></span>Restarting...';
        document.getElementById('update-banner-subtitle').textContent =
            'The application is restarting with the new version.';

        const actionsDiv = document.getElementById('update-banner-actions');
        actionsDiv.innerHTML = `
            <button class="btn btn-sm btn-light" onclick="AIHubUpdater.refreshNow()">
                Refresh Now
            </button>
        `;
        actionsDiv.style.display = 'block';
        document.getElementById('update-banner-progress').style.display = 'none';
    }

    async function pollForRestart() {
        if (!restartPollStart) {
            restartPollStart = Date.now();
        }

        const elapsed = Date.now() - restartPollStart;

        if (elapsed > config.restartTimeoutMs) {
            // Timed out - show manual fallback options
            showManualInstallFallback();
            restartPollStart = 0;
            return;
        }

        try {
            // Try to reach the version endpoint
            const response = await fetch(`${config.apiBase}/version`, {
                method: 'GET',
                cache: 'no-store'
            });

            if (response.ok) {
                const data = await response.json();

                // Check if it's the new version
                if (updateInfo && data.version === updateInfo.latestVersion) {
                    // Successfully updated!
                    document.getElementById('update-banner-title').innerHTML =
                        '&#x2714; Update Complete!';
                    document.getElementById('update-banner-subtitle').textContent =
                        `Now running version ${data.version}. Refreshing...`;
                    document.getElementById('update-banner-actions').style.display = 'none';

                    // Auto-refresh after brief delay
                    setTimeout(() => {
                        window.location.reload();
                    }, 1500);

                    restartPollStart = 0;
                    return;
                }

                // Server is up but still old version - installer may not be done yet
                // or NSSM hasn't restarted yet. Keep polling.
            }
        } catch (e) {
            // App is still restarting, this is expected - continue polling silently
        }

        // Continue polling
        setTimeout(pollForRestart, config.restartPollMs);
    }

    function refreshNow() {
        window.location.reload();
    }

    // ========================================================================
    // UI UPDATES
    // ========================================================================

    function showBanner() {
        if (!updateInfo) return;

        // Check if user dismissed this version
        const dismissed = sessionStorage.getItem('update_dismissed');
        if (dismissed === updateInfo.latestVersion) return;

        document.getElementById('update-banner-title').textContent = 'Update Available';
        document.getElementById('update-banner-subtitle').textContent =
            `Version ${updateInfo.latestVersion} is ready (you have ${updateInfo.currentVersion})`;

        document.getElementById('update-banner').style.display = 'block';
        document.body.classList.add('has-update-banner');
    }

    function showInstallReady() {
        document.getElementById('update-banner-progress').style.display = 'none';
        document.getElementById('update-banner-title').textContent = '\u2713 Download Complete';
        document.getElementById('update-banner-subtitle').textContent = '';

        const actionsDiv = document.getElementById('update-banner-actions');
        actionsDiv.innerHTML = `
            <span style="margin-right: 10px;">Ready to install version ${updateInfo.latestVersion}</span>
            <button class="btn btn-sm btn-primary" onclick="AIHubUpdater.install()">
                Install &amp; Restart
            </button>
            <button class="btn btn-sm btn-light" onclick="AIHubUpdater.dismiss()">
                Later
            </button>
        `;
        actionsDiv.style.display = 'flex';
    }

    function showError(message, showFolder = false) {
        const banner = document.getElementById('update-banner');
        banner.classList.remove('installing');
        banner.classList.add('critical');

        document.querySelector('.update-banner-icon').innerHTML = '&#x26A0;';
        document.getElementById('update-banner-progress').style.display = 'none';
        document.getElementById('update-banner-title').textContent = 'Update Error';
        document.getElementById('update-banner-subtitle').textContent = message;

        const actionsDiv = document.getElementById('update-banner-actions');
        let html = `
            <button class="btn btn-sm btn-light" onclick="AIHubUpdater.check()">Try Again</button>
        `;

        if (showFolder && installerPath) {
            html += `
                <button class="btn btn-sm btn-light" onclick="AIHubUpdater.openInstallerFolder()" title="Show the installer file path to run it manually">
                    <i class="fas fa-folder-open mr-1"></i>Show Installer Path
                </button>
            `;
        }

        html += `
            <button class="btn btn-sm btn-link text-white" onclick="AIHubUpdater.dismiss()">Dismiss</button>
        `;

        actionsDiv.innerHTML = html;
        actionsDiv.style.display = 'flex';
    }

    async function openInstallerFolder() {
        try {
            const response = await fetch(`${config.apiBase}/open-folder`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });

            const data = await response.json();

            if (data.status === 'success') {
                // Show the path as confirmation (explorer may not appear in Session 0)
                showInstallerPath(data.folder_path, data.file_name);
            } else {
                // Fallback: show the path from our local state
                if (installerPath) {
                    showInstallerPath(
                        installerPath.substring(0, installerPath.lastIndexOf('\\')),
                        installerPath.substring(installerPath.lastIndexOf('\\') + 1)
                    );
                }
            }
        } catch (error) {
            console.error('Failed to open installer folder:', error);
            if (installerPath) {
                showInstallerPath(
                    installerPath.substring(0, installerPath.lastIndexOf('\\')),
                    installerPath.substring(installerPath.lastIndexOf('\\') + 1)
                );
            }
        }
    }

    function showInstallerPath(folderPath, fileName) {
        // Show the path inline so the user can navigate there manually
        // (explorer.exe can't render when Flask is in Session 0)
        const subtitle = document.getElementById('update-banner-subtitle');
        const displayPath = folderPath ? (folderPath + '\\' + (fileName || '')) : installerPath;
        subtitle.innerHTML =
            'Navigate to: <strong style="user-select:all; cursor:text;">' +
            displayPath + '</strong>';
    }

    function showManualInstallFallback() {
        document.getElementById('update-banner-title').innerHTML = 'Update may be complete';
        document.getElementById('update-banner-subtitle').textContent =
            'The restart is taking longer than expected.';

        const actionsDiv = document.getElementById('update-banner-actions');
        let html = '';

        if (installerPath) {
            html = `
                <button class="btn btn-sm btn-light" onclick="AIHubUpdater.openInstallerFolder()" title="Show the installer file path">
                    <i class="fas fa-folder-open mr-1"></i>Show Installer Path
                </button>
                <button class="btn btn-sm btn-primary" onclick="AIHubUpdater.refreshNow()">
                    Refresh Now
                </button>
            `;
        } else {
            html = `
                <button class="btn btn-sm btn-primary" onclick="AIHubUpdater.refreshNow()">
                    Refresh Now
                </button>
            `;
        }

        actionsDiv.innerHTML = html;
        actionsDiv.style.display = 'flex';
    }

    function hideBanner() {
        const banner = document.getElementById('update-banner');
        banner.style.display = 'none';
        banner.classList.remove('installing', 'critical');
        document.body.classList.remove('has-update-banner');
    }

    function dismiss() {
        if (updateInfo) {
            sessionStorage.setItem('update_dismissed', updateInfo.latestVersion);
        }
        hideBanner();
    }

    function showNotes() {
        if (!updateInfo) return;

        document.getElementById('notes-modal-version').textContent = `Version ${updateInfo.latestVersion}`;

        // Render markdown as HTML
        if (typeof marked !== 'undefined') {
            document.getElementById('notes-modal-content').innerHTML = marked.parse(updateInfo.releaseNotes);
        } else {
            // Fallback to plain text if marked isn't loaded
            document.getElementById('notes-modal-content').textContent = updateInfo.releaseNotes;
        }

        // Use Bootstrap modal
        if (typeof $ !== 'undefined' && $.fn.modal) {
            $('#updateNotesModal').modal('show');
        }
    }

    // ========================================================================
    // FORMATTING HELPERS
    // ========================================================================

    function formatFileSize(bytes) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    }

    // ========================================================================
    // INITIALIZATION
    // ========================================================================

    function init() {
        // Only show update banner to admins (role >= 3)
        const userRole = window.AIHUB_USER_ROLE || 0;
        if (userRole < 3) {
            return;  // Don't initialize for non-admins
        }

        injectUI();

        if (config.checkOnLoad) {
            // Delay check to not interfere with page load
            setTimeout(checkForUpdate, config.checkDelayMs);
        }
    }

    // Auto-initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // ========================================================================
    // PUBLIC API
    // ========================================================================

    return {
        check: () => checkForUpdate(false),
        forceCheck: () => checkForUpdate(true),
        download: startDownload,
        install: installUpdate,
        dismiss: dismiss,
        showNotes: showNotes,
        refreshNow: refreshNow,
        openInstallerFolder: openInstallerFolder,
        getUpdateInfo: () => updateInfo,
        isDownloading: () => isDownloading,
        isDownloadComplete: () => downloadComplete
    };

})();

// Make available globally
window.AIHubUpdater = AIHubUpdater;
