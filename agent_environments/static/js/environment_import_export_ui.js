
class EnvironmentImportExport {
    constructor() {
        this.initializeUI();
    }
    
    initializeUI() {
        // Add import button to the main environment list page
        this.addImportButton();
        
        // Add export buttons to each environment card
        this.addExportButtons();
        
        // Initialize modal for import
        this.createImportModal();
    }
    
    addImportButton() {
        // Add import button next to the "Create Environment" button
        const importBtn = `
            <button class="btn btn-info" onclick="environmentImportExport.showImportModal()">
                <i class="fas fa-file-import"></i> Import Environment
            </button>
        `;
        
        // Insert after create button
        const createBtn = document.querySelector('button[data-target="#createEnvironmentModal"]');
        if (createBtn) {
            createBtn.insertAdjacentHTML('afterend', ' ' + importBtn);
        }
    }
    
    addExportButtons() {
        // This should be called when environment cards are rendered
        // Add to each environment card's action buttons
        document.querySelectorAll('.environment-card').forEach(card => {
            const envId = card.dataset.environmentId;
            if (envId) {
                const actionsDiv = card.querySelector('.card-actions');
                if (actionsDiv) {
                    const exportBtn = `
                        <button class="btn btn-sm btn-outline-primary" 
                                onclick="environmentImportExport.exportEnvironment('${envId}')"
                                title="Export Environment">
                            <i class="fas fa-download"></i> Export
                        </button>
                    `;
                    actionsDiv.insertAdjacentHTML('beforeend', exportBtn);
                }
            }
        });
    }
    
    createImportModal() {
        const modalHtml = `
            <div class="modal fade" id="importEnvironmentModal" tabindex="-1" role="dialog">
                <div class="modal-dialog modal-lg" role="document">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">Import Environment</h5>
                            <button type="button" class="close" data-dismiss="modal">
                                <span>&times;</span>
                            </button>
                        </div>
                        <div class="modal-body">
                            <div id="importStep1" class="import-step">
                                <h6>Step 1: Select Environment Package</h6>
                                <div class="form-group">
                                    <label for="importFile">Choose ZIP file to import:</label>
                                    <input type="file" class="form-control-file" id="importFile" 
                                           accept=".zip" onchange="environmentImportExport.analyzePackage()">
                                </div>
                                <div id="importFileInfo" class="alert alert-info" style="display:none;"></div>
                            </div>
                            
                            <div id="importStep2" class="import-step" style="display:none;">
                                <h6>Step 2: Review Package Contents</h6>
                                <div id="packageAnalysis" class="package-analysis"></div>
                            </div>
                            
                            <div id="importStep3" class="import-step" style="display:none;">
                                <h6>Step 3: Import Settings</h6>
                                <form id="importSettingsForm">
                                    <div class="form-group">
                                        <label for="importEnvName">Environment Name:</label>
                                        <input type="text" class="form-control" id="importEnvName" 
                                               placeholder="Leave empty to use original name">
                                        <small class="form-text text-muted">
                                            A unique name will be generated if the original name already exists
                                        </small>
                                    </div>
                                    
                                    <div class="form-group">
                                        <div class="custom-control custom-switch">
                                            <input type="checkbox" class="custom-control-input" 
                                                   id="skipPackages">
                                            <label class="custom-control-label" for="skipPackages">
                                                Skip package installation (create empty environment)
                                            </label>
                                        </div>
                                        <small class="form-text text-muted">
                                            Enable this if you want to manually install packages later
                                        </small>
                                    </div>
                                </form>
                            </div>
                            
                            <div id="importProgress" class="import-progress" style="display:none;">
                                <div class="progress">
                                    <div class="progress-gradient">
                                        <div class="progress-bar-gradient" style="width: 100%;"></div>
                                    </div>
                                </div>
                            </div>
                            
                            <div id="importResult" class="import-result" style="display:none;"></div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-dismiss="modal">Cancel</button>
                            <button type="button" class="btn btn-primary" id="importBtn" 
                                    onclick="environmentImportExport.performImport()" 
                                    style="display:none;">Import Environment</button>
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        // Add modal to body if not exists
        if (!document.getElementById('importEnvironmentModal')) {
            document.body.insertAdjacentHTML('beforeend', modalHtml);
        }
    }
    
    showImportModal() {
        // Reset modal state
        this.resetImportModal();
        $('#importEnvironmentModal').modal('show');
    }
    
    resetImportModal() {
        document.getElementById('importFile').value = '';
        document.getElementById('importEnvName').value = '';
        document.getElementById('skipPackages').checked = false;
        document.getElementById('importFileInfo').style.display = 'none';
        document.getElementById('importStep2').style.display = 'none';
        document.getElementById('importStep3').style.display = 'none';
        document.getElementById('importBtn').style.display = 'none';
        document.getElementById('importProgress').style.display = 'none';
        document.getElementById('importResult').style.display = 'none';
        this.selectedFile = null;
        this.packageAnalysisData = null;
    }
    
    async analyzePackage() {
        const fileInput = document.getElementById('importFile');
        const file = fileInput.files[0];
        
        if (!file) {
            return;
        }
        
        this.selectedFile = file;
        
        // Show file info
        const fileInfo = document.getElementById('importFileInfo');
        fileInfo.innerHTML = `
            <strong>Selected file:</strong> ${file.name}<br>
            <strong>Size:</strong> ${(file.size / 1024).toFixed(2)} KB
        `;
        fileInfo.style.display = 'block';
        
        // Analyze package
        const formData = new FormData();
        formData.append('file', file);
        
        try {
            const response = await fetch('/environments/api/import/analyze', {
                method: 'POST',
                body: formData
            });
            
            const data = await response.json();
            
            if (data.status === 'success') {
                this.packageAnalysisData = data.analysis;
                this.displayPackageAnalysis(data.analysis);
                
                // Show next steps
                document.getElementById('importStep2').style.display = 'block';
                document.getElementById('importStep3').style.display = 'block';
                document.getElementById('importBtn').style.display = 'inline-block';
                
                // Pre-fill environment name if available
                if (data.analysis.environment.name) {
                    document.getElementById('importEnvName').placeholder = 
                        `Original: ${data.analysis.environment.name}`;
                }
            } else {
                this.showError(data.message || 'Failed to analyze package');
            }
        } catch (error) {
            console.error('Error analyzing package:', error);
            this.showError('Failed to analyze package: ' + error.message);
        }
    }
    
    displayPackageAnalysis(analysis) {
        let html = '<div class="card">';
        html += '<div class="card-body">';
        
        // Environment info
        html += '<h6>Environment Information:</h6>';
        html += '<ul class="list-unstyled">';
        html += `<li><strong>Name:</strong> ${analysis.environment.name || 'N/A'}</li>`;
        html += `<li><strong>Description:</strong> ${analysis.environment.description || 'N/A'}</li>`;
        html += `<li><strong>Python Version:</strong> ${analysis.environment.python_version || 'Default'}</li>`;
        html += `<li><strong>Created:</strong> ${analysis.environment.created_date ? new Date(analysis.environment.created_date).toLocaleDateString() : 'N/A'}</li>`;
        html += '</ul>';
        
        // Package info
        html += `<h6>Packages (${analysis.package_count}):</h6>`;
        if (analysis.packages.length > 0) {
            html += '<div class="package-list" style="max-height: 200px; overflow-y: auto;">';
            html += '<table class="table table-sm">';
            html += '<thead><tr><th>Package</th><th>Version</th></tr></thead>';
            html += '<tbody>';
            
            // Show first 10 packages
            const packagesToShow = analysis.packages.slice(0, 10);
            packagesToShow.forEach(pkg => {
                html += `<tr><td>${pkg.name}</td><td>${pkg.version || 'latest'}</td></tr>`;
            });
            
            if (analysis.packages.length > 10) {
                html += `<tr><td colspan="2">... and ${analysis.packages.length - 10} more</td></tr>`;
            }
            
            html += '</tbody></table>';
            html += '</div>';
        } else {
            html += '<p class="text-muted">No packages found</p>';
        }
        
        // Warnings
        if (analysis.warnings && analysis.warnings.length > 0) {
            html += '<div class="alert alert-warning mt-3">';
            html += '<strong>Warnings:</strong>';
            html += '<ul class="mb-0">';
            analysis.warnings.forEach(warning => {
                html += `<li>${warning}</li>`;
            });
            html += '</ul>';
            html += '</div>';
        }
        
        html += '</div></div>';
        
        document.getElementById('packageAnalysis').innerHTML = html;
    }
    
    async performImport() {
        if (!this.selectedFile) {
            this.showError('No file selected');
            return;
        }
        
        // Show progress
        document.getElementById('importProgress').style.display = 'block';
        document.getElementById('importBtn').disabled = true;

        // Animate the progress bar
        // const progressBar = document.querySelector('#importProgress .progress-bar');
        // progressBar.style.width = '100%';
        // progressBar.classList.add('progress-bar-striped', 'progress-bar-animated');
        
        // Prepare form data
        const formData = new FormData();
        formData.append('file', this.selectedFile);
        
        const envName = document.getElementById('importEnvName').value;
        if (envName) {
            formData.append('name', envName);
        }
        
        if (document.getElementById('skipPackages').checked) {
            formData.append('skip_packages', 'true');
        }
        
        try {
            const response = await fetch('/environments/api/import', {
                method: 'POST',
                body: formData
            });
            
            const data = await response.json();
            
            document.getElementById('importProgress').style.display = 'none';
            
            if (data.status === 'success') {
                // Show success message
                const resultDiv = document.getElementById('importResult');
                resultDiv.className = 'alert alert-success';
                resultDiv.innerHTML = `
                    <i class="fas fa-check-circle"></i> ${data.message}
                    <br><br>
                    <a href="/environments/editor/${data.environment_id}" class="btn btn-sm btn-primary">
                        <i class="fas fa-edit"></i> Open Environment
                    </a>
                `;
                resultDiv.style.display = 'block';
                
                // Reload environment list after 3 seconds
                setTimeout(() => {
                    location.reload();
                }, 3000);
            } else {
                this.showError(data.message || 'Failed to import environment');
                document.getElementById('importBtn').disabled = false;
            }
        } catch (error) {
            console.error('Error importing environment:', error);
            document.getElementById('importProgress').style.display = 'none';
            this.showError('Failed to import environment: ' + error.message);
            document.getElementById('importBtn').disabled = false;
        }
    }
    
    async exportEnvironment(envId) {
        try {
            // Create a temporary link and trigger download
            const link = document.createElement('a');
            link.href = `/environments/api/${envId}/export`;
            link.download = '';  // Browser will use the filename from response
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            
            this.showSuccess('Environment export started');
        } catch (error) {
            console.error('Error exporting environment:', error);
            this.showError('Failed to export environment: ' + error.message);
        }
    }
    
    async exportRequirements(envId) {
        try {
            // Create a temporary link and trigger download
            const link = document.createElement('a');
            link.href = `/environments/api/${envId}/export/requirements`;
            link.download = '';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            
            this.showSuccess('Requirements file downloaded');
        } catch (error) {
            console.error('Error exporting requirements:', error);
            this.showError('Failed to export requirements: ' + error.message);
        }
    }
    
    showSuccess(message) {
        // You can customize this to use your existing notification system
        const alert = `
            <div class="alert alert-success alert-dismissible fade show" role="alert">
                ${message}
                <button type="button" class="close" data-dismiss="alert">
                    <span>&times;</span>
                </button>
            </div>
        `;
        
        const container = document.querySelector('.alert-container') || document.body;
        container.insertAdjacentHTML('afterbegin', alert);
        
        // Auto-dismiss after 5 seconds
        setTimeout(() => {
            const alertEl = container.querySelector('.alert');
            if (alertEl) {
                alertEl.remove();
            }
        }, 5000);
    }
    
    showError(message) {
        // You can customize this to use your existing notification system
        const alert = `
            <div class="alert alert-danger alert-dismissible fade show" role="alert">
                ${message}
                <button type="button" class="close" data-dismiss="alert">
                    <span>&times;</span>
                </button>
            </div>
        `;
        
        const container = document.querySelector('.alert-container') || document.body;
        container.insertAdjacentHTML('afterbegin', alert);
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    window.environmentImportExport = new EnvironmentImportExport();
});