// environment_manager.js - Fixed version
class EnvironmentManager {
    constructor() {
        this.environments = [];
        this.selectedEnvironment = null;
        this.init();
    }
    
    init() {
        this.loadEnvironments();
        this.setupEventListeners();
        
        // Refresh every 30 seconds
        setInterval(() => this.loadEnvironments(), 30000);
    }
    
    setupEventListeners() {
        // Form submission
        document.getElementById('createEnvironmentForm')?.addEventListener('submit', (e) => {
            e.preventDefault();
            this.createEnvironment();
        });
    }
    
    async loadEnvironments() {
        try {
            const response = await fetch('/environments/api/list');
            const data = await response.json();
            
            if (data.status === 'success') {
                this.environments = data.environments || [];
                this.renderEnvironments();
                this.updateStats();
            } else {
                console.error('Failed to load environments:', data.message);
                this.showError(data.message || 'Failed to load environments');
            }
        } catch (error) {
            console.error('Error loading environments:', error);
            this.showError('Failed to load environments: ' + error.message);
        }
    }
    
    renderEnvironments() {
        const container = document.getElementById('environmentList');
        
        if (!container) {
            console.error('Container #environmentList not found');
            return;
        }
        
        if (!this.environments || this.environments.length === 0) {
            container.innerHTML = `
                <div class="text-center py-4">
                    <p class="text-muted">No environments created yet</p>
                    <button class="btn btn-primary" onclick="showCreateModal()">
                        Create Your First Environment
                    </button>
                </div>
            `;
            return;
        }
        
        const html = this.environments.map(env => {
            // Safely handle packages - it might be a count or an object
            let packageCount = 0;
            if (env.package_count !== undefined) {
                packageCount = env.package_count;
            } else if (env.packages && typeof env.packages === 'object') {
                packageCount = Array.isArray(env.packages) ? env.packages.length : Object.keys(env.packages).length;
            }
            
            // Safely handle agent count
            const agentCount = env.agent_count || 0;
            
            // Format dates safely
            const createdDate = env.created_date ? new Date(env.created_date).toLocaleDateString() : 'Unknown';
            
            // Determine status class
            const statusClass = env.status === 'active' ? 'success' : 
                              env.status === 'pending' ? 'warning' : 'danger';
            
            return `
                <div class="environment-card mb-3" data-env-id="${env.environment_id}">
                    <div class="card">
                        <div class="card-body">
                            <div class="row">
                                <div class="col-md-8">
                                    <h5 class="card-title">
                                        <span class="badge badge-${statusClass} badge-sm mr-2">
                                            ${env.status || 'unknown'}
                                        </span>
                                        ${env.name}
                                    </h5>
                                    <p class="card-text text-muted">${env.description || 'No description'}</p>
                                    <small class="text-muted">
                                        Created: ${createdDate}
                                        | Packages: ${packageCount}
                                        | Used by: ${agentCount} agent(s)
                                    </small>
                                </div>
                                <div class="col-md-4 text-right">
                                    <button class="btn btn-sm btn-primary" 
                                            onclick="envManager.editEnvironment('${env.environment_id}')">
                                        <i class="fas fa-edit"></i> Edit
                                    </button>
                                    <button class="btn btn-sm btn-info" 
                                            onclick="envManager.cloneEnvironment('${env.environment_id}')">
                                        <i class="fas fa-clone"></i> Clone
                                    </button>
                                    <button class="btn btn-sm btn-info" onclick="environmentImportExport.exportEnvironment('${env.environment_id}')">
                                        <i class="fas fa-download"></i> Export
                                    </button>
                                    <button class="btn btn-sm btn-danger" 
                                            onclick="envManager.deleteEnvironment('${env.environment_id}')"
                                            ${agentCount > 0 ? 'disabled title="Cannot delete: environment is in use"' : ''}>
                                        <i class="fas fa-trash"></i> Delete
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        }).join('');
        
        container.innerHTML = html;
    }
    
    updateStats() {
        // Safely update statistics
        const totalEnvs = this.environments ? this.environments.length : 0;
        document.getElementById('totalEnvs').textContent = totalEnvs;
        
        // Calculate total agents safely
        const totalAgents = this.environments ? 
            this.environments.reduce((sum, env) => sum + (env.agent_count || 0), 0) : 0;
        document.getElementById('activeAgents').textContent = totalAgents;
        
        // Calculate total packages safely
        const totalPackages = this.environments ? 
            this.environments.reduce((sum, env) => {
                if (env.package_count !== undefined) {
                    return sum + env.package_count;
                } else if (env.packages) {
                    if (Array.isArray(env.packages)) {
                        return sum + env.packages.length;
                    } else if (typeof env.packages === 'object') {
                        return sum + Object.keys(env.packages).length;
                    }
                }
                return sum;
            }, 0) : 0;
        document.getElementById('totalPackages').textContent = totalPackages;
        
        // Update usage bar if max_environments is available
        if (window.max_environments && window.max_environments > 0) {
            const usagePercent = (totalEnvs / window.max_environments) * 100;
            const usageBar = document.getElementById('envUsageBar');
            if (usageBar) {
                usageBar.style.width = usagePercent + '%';
                usageBar.setAttribute('aria-valuenow', usagePercent);
                
                // Change color based on usage
                usageBar.classList.remove('bg-success', 'bg-warning', 'bg-danger');
                if (usagePercent < 60) {
                    usageBar.classList.add('bg-success');
                } else if (usagePercent < 90) {
                    usageBar.classList.add('bg-warning');
                } else {
                    usageBar.classList.add('bg-danger');
                }
            }
        }
    }
    
    async createEnvironment() {
        const name = document.getElementById('envName').value;
        const description = document.getElementById('envDescription').value;
        
        if (!name) {
            this.showError('Environment name is required');
            return;
        }
        
        // Get selected initial packages
        const packages = [];
        document.querySelectorAll('#initialPackages input:checked').forEach(checkbox => {
            packages.push(checkbox.value);
        });

        // Disable the create button and show loading state
        const createBtn = document.querySelector('#createEnvironmentModal .btn-primary');
        const originalBtnText = createBtn.innerHTML;
        createBtn.disabled = true;
        createBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Creating environment...';
        
        // Show progress message in modal body
        const progressDiv = document.createElement('div');
        progressDiv.className = 'alert alert-info mt-3';
        progressDiv.innerHTML = `
            <div class="d-flex align-items-center">
                <div class="processing-animation mb-3 mr-3" role="status">
                    <div class="processing-spinner"></div>
                </div>

                <div>
                    <strong>Creating environment...</strong><br>
                    <small>This may take several minutes.</small>
                </div>
            </div>
        `;
        progressDiv.id = 'creationProgress';
        
        const modalBody = document.querySelector('#createEnvironmentModal .modal-body');
        modalBody.appendChild(progressDiv);
        
        try {
            const response = await fetch('/environments/api/create', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    name: name,
                    description: description,
                    initial_packages: packages
                })
            });
            
            const data = await response.json();
            
            if (data.status === 'success') {


                    // Update progress message to success
                    progressDiv.className = 'alert alert-success mt-3';
                    progressDiv.innerHTML = `
                        <div class="d-flex align-items-center">
                            <i class="fas fa-check-circle mr-2"></i>
                            <div>
                                <strong>Environment created successfully!</strong><br>
                                <small>You may close this window.</small>
                            </div>
                        </div>
                    `;
                    
                    // Wait a moment so user can see success message
                    //setTimeout(() => {
                        //$('#createEnvironmentModal').modal('hide');

                        if (document.getElementById('createCancelButton')) {
                            document.getElementById('createCancelButton').innerHTML = "Close";
                        }
                        // Reset button
                        //createBtn.disabled = false;

                        createBtn.innerHTML = originalBtnText;
                        // Reload environments
                        this.loadEnvironments();
                    //}, 1500);

            } else {
                // Show error in progress div
                progressDiv.className = 'alert alert-danger mt-3';
                progressDiv.innerHTML = `
                    <div class="d-flex align-items-center">
                        <i class="fas fa-exclamation-circle mr-2"></i>
                        <div>
                            <strong>Failed to create environment</strong><br>
                            <small>${data.message || 'An error occurred'}</small>
                        </div>
                    </div>
                `;
                
                // Re-enable button
                createBtn.disabled = false;
                createBtn.innerHTML = originalBtnText;
            }
        } catch (error) {
            console.error('Error creating environment:', error);
            
            // Show error in progress div
            progressDiv.className = 'alert alert-danger mt-3';
            progressDiv.innerHTML = `
                <div class="d-flex align-items-center">
                    <i class="fas fa-exclamation-circle mr-2"></i>
                    <div>
                        <strong>Failed to create environment</strong><br>
                        <small>${error.message}</small>
                    </div>
                </div>
            `;
            
            // Re-enable button
            createBtn.disabled = false;
            createBtn.innerHTML = originalBtnText;
        }
    }

    
async createEnvironmentWithProgress() {
    console.log(`Called create evironment with progress...`);
    const name = document.getElementById('envName').value;
        const description = document.getElementById('envDescription').value;
        
        if (!name) {
            this.showError('Environment name is required');
            return;
        }
        
        // Get selected initial packages
        const packages = [];
        document.querySelectorAll('#initialPackages input:checked').forEach(checkbox => {
            packages.push(checkbox.value);
        });

    const data = {
                    name: name,
                    description: description,
                    initial_packages: packages
                }
        
    console.log(`Running envManager.createEnvironmentWithProgress with data ${data}`);

    const progressDivSpin = document.getElementById('progress-container-spinner');
    const progressDiv = document.getElementById('progress-container');
    const progressBar = document.getElementById('progress-bar');
    const progressText = document.getElementById('progress-text');
    const progressMessage = document.getElementById('progress-message');

    const createBtn = document.querySelector('#createEnvironmentModal .btn-primary');
    createBtn.disabled = true;
    
    // Show progress container
    progressDivSpin.style.display = 'block';
    progressDiv.style.display = 'block';

    // Add progress spinner
    const progressDivSpinner = document.createElement('div');
    progressDivSpinner.className = 'alert alert-info mt-3';
    progressDivSpinner.innerHTML = `
        <div class="d-flex align-items-center">
            <div class="processing-animation mb-3 mr-3" role="status">
                <div class="processing-spinner"></div>
            </div>

            <div>
                <strong>Creating environment...</strong><br>
                <small>This may take several minutes.</small>
            </div>
        </div>
    `;
    progressDivSpinner.id = 'creationProgress';
    progressDivSpin.appendChild(progressDivSpinner);
    
    // Alternative: Use fetch with streaming (better for POST requests)
    fetch('/environments/api/create-stream', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(data)
    }).then(response => {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        console.log(`Running processStream...`);
        function processStream() {
            reader.read().then(({ done, value }) => {
                if (done) return;
                
                const chunk = decoder.decode(value);
                const lines = chunk.split('\n');
                
                lines.forEach(line => {
                    if (line.startsWith('data: ')) {
                        const data = JSON.parse(line.substring(6));
                        
                        // Update UI
                        progressBar.style.width = data.progress + '%';
                        progressBar.setAttribute('aria-valuenow', data.progress);
                        progressText.textContent = data.progress + '%';
                        progressMessage.textContent = data.message;
                        
                        // Add step indicator
                        const stepIndicator = document.getElementById('step-' + data.step.toLowerCase().replace(' ', '-'));
                        if (stepIndicator) {
                            stepIndicator.classList.add('active');
                        }
                        
                        if (data.complete) {
                            // Success - redirect or update UI
                            progressMessage.classList.add('text-success');

                            // setTimeout(() => {
                            //     window.location.href = `/environments/editor/${data.env_id}`;
                            // }, 1500);

                            // Highlight last step
                            const stepIndicatorFinal = document.getElementById('step-finalization');
                            if (stepIndicatorFinal) {
                                stepIndicatorFinal.classList.add('active');
                            }

                            // Remove spinner
                            if (progressDivSpinner) {
                                progressDivSpinner.remove();
                            }

                            if (document.getElementById('createCancelButton')) {
                                document.getElementById('createCancelButton').innerHTML = "Close";
                            }
                        } else if (data.error) {
                            // Error - show message
                            progressMessage.classList.add('text-danger');
                            progressBar.classList.add('bg-danger');

                            if (progressDivSpinner) {
                                progressDivSpinner.remove();
                            }
                        }
                    }
                });
                
                // Continue reading
                processStream();
            });
        }
        
        processStream();
    }).catch(error => {
        console.error('Stream error:', error);
        progressMessage.textContent = 'Connection error: ' + error.message;
        progressMessage.classList.add('text-danger');
        if (progressDivSpinner) {
            progressDivSpinner.remove();
        }
    });
}


    
    editEnvironment(envId) {
        // Redirect to editor page
        window.location.href = `/environments/editor/${envId}`;
    }
    
    async cloneEnvironment(envId) {
        const name = prompt('Enter name for the cloned environment:');
        if (!name) return;
        
        try {
            const response = await fetch(`/environments/api/${envId}/clone`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ name: name })
            });
            
            const data = await response.json();
            
            if (data.status === 'success') {
                this.showSuccess('Environment cloned successfully');
                this.loadEnvironments();
            } else {
                this.showError(data.message || 'Failed to clone environment');
            }
        } catch (error) {
            console.error('Error cloning environment:', error);
            this.showError('Failed to clone environment: ' + error.message);
        }
    }
    
    async deleteEnvironment(envId) {
        if (!confirm('Are you sure you want to delete this environment? This action cannot be undone.')) {
            return;
        }
        
        try {
            const response = await fetch(`/environments/api/${envId}`, {
                method: 'DELETE'
            });
            
            const data = await response.json();
            
            if (data.status === 'success') {
                this.showSuccess('Environment deleted successfully');
                this.loadEnvironments();
            } else {
                this.showError(data.message || 'Failed to delete environment');
            }
        } catch (error) {
            console.error('Error deleting environment:', error);
            this.showError('Failed to delete environment: ' + error.message);
        }
    }
    
    showSuccess(message) {
        this.showNotification(message, 'success');
    }
    
    showError(message) {
        this.showNotification(message, 'danger');
    }
    
    showNotification(message, type = 'info') {
        // Create notification element
        const notification = document.createElement('div');
        notification.className = `alert alert-${type} alert-dismissible fade show`;
        notification.style.cssText = 'position: fixed; top: 20px; right: 20px; z-index: 9999; min-width: 250px;';
        notification.innerHTML = `
            ${message}
            <button type="button" class="close" data-dismiss="alert">
                <span>&times;</span>
            </button>
        `;
        
        document.body.appendChild(notification);
        
        // Auto-remove after 5 seconds
        setTimeout(() => {
            notification.classList.remove('show');
            setTimeout(() => {
                document.body.removeChild(notification);
            }, 150);
        }, 5000);
    }
}

// Initialize manager when DOM is ready
let envManager;

document.addEventListener('DOMContentLoaded', function() {
    envManager = new EnvironmentManager();
});

function resetCreateModal() {
    console.log('Resetting environment creation form...');

    // Reset custom progress spinner
    const progressDivSpin = document.getElementById('progress-container-spinner');
    progressDivSpin.style.display = 'none';

    // Remove progress div (if exists)
    if (document.getElementById('creationProgress')) {
        document.getElementById('creationProgress').remove();
    }
    
    // 1. Reset the form fields
    const form = document.getElementById('createEnvironmentForm');
    if (form) {
        form.reset();  // Clears all input fields and unchecks checkboxes
    }
    
    // 2. Reset the progress container
    const progressContainer = document.getElementById('progress-container');
    if (progressContainer) {
        progressContainer.style.display = 'none';  // Hide it
    }
    
    // 3. Reset progress bar
    const progressBar = document.getElementById('progress-bar');
    if (progressBar) {
        progressBar.style.width = '0%';
        progressBar.setAttribute('aria-valuenow', '0');
        progressBar.classList.remove('bg-danger', 'bg-success', 'bg-warning');  // Remove any color classes
        progressBar.classList.add('progress-bar-striped', 'progress-bar-animated');  // Restore default classes if needed
    }
    
    // 4. Reset progress text
    const progressText = document.getElementById('progress-text');
    if (progressText) {
        progressText.textContent = '0%';
    }
    
    // 5. Reset progress message
    const progressMessage = document.getElementById('progress-message');
    if (progressMessage) {
        progressMessage.textContent = '';
        progressMessage.className = '';  // Clear all classes
        progressMessage.classList.add('text-muted');  // Add default class
    }
    
    // 6. Reset step indicators
    const stepIndicators = document.querySelectorAll('.step-indicators .badge');
    stepIndicators.forEach(indicator => {
        indicator.classList.remove('active', 'badge-success', 'badge-primary', 'badge-danger');
        indicator.classList.add('badge-secondary');
    });
    
    // 7. Reset the create button
    const createBtn = document.querySelector('#createEnvironmentModal .btn-primary');
    if (createBtn) {
        createBtn.disabled = false;
        createBtn.innerHTML = 'Create Environment';  // Original text
        createBtn.className = 'btn btn-primary';  // Reset all classes
    }
    
    // 8. Reset the cancel button text
    const cancelBtn = document.getElementById('createCancelButton');
    if (cancelBtn) {
        cancelBtn.innerHTML = 'Cancel';  // Reset from "Close" back to "Cancel"
    }
    
    // 9. Remove any lingering alerts or error messages
    const existingAlerts = document.querySelectorAll('#createEnvironmentModal .alert');
    existingAlerts.forEach(alert => alert.remove());
    
    // 10. Clear any validation error messages
    const errorElements = document.querySelectorAll('#createEnvironmentModal .is-invalid');
    errorElements.forEach(element => {
        element.classList.remove('is-invalid');
    });
    
    const feedbackElements = document.querySelectorAll('#createEnvironmentModal .invalid-feedback');
    feedbackElements.forEach(element => {
        element.style.display = 'none';
    });
    
    console.log('Form reset complete');
}

// Global functions for onclick handlers
function showCreateModal() {
    resetCreateModal();
    $('#createEnvironmentModal').modal('show');
}

function createEnvironment() {
    //envManager.createEnvironment();
    envManager.createEnvironmentWithProgress();
}

function showTemplateModal() {
    // TODO: Implement template selection modal
    alert('Template feature coming soon!');
}

function importEnvironment() {
    // TODO: Implement import functionality
    alert('Import feature coming soon!');
}

// In environment_manager.js or inline script
function showDocumentation() {
    window.open('/environments/docs', '_blank');
}

function showCleanupModal() {
    if (confirm('This will remove all unused environments. Are you sure?')) {
        // TODO: Implement cleanup
        alert('Cleanup feature coming soon!');
    }
}

function showUsageReport() {
    // TODO: Implement usage report
    alert('Usage report feature coming soon!');
}


function createEnvironmentWithProgress() {
    const name = document.getElementById('envName').value;
        const description = document.getElementById('envDescription').value;
        
        if (!name) {
            this.showError('Environment name is required');
            return;
        }
        
        // Get selected initial packages
        const packages = [];
        document.querySelectorAll('#initialPackages input:checked').forEach(checkbox => {
            packages.push(checkbox.value);
        });

    const data = {
                    name: name,
                    description: description,
                    initial_packages: packages
                }
        
    const progressDiv = document.getElementById('progress-container');
    const progressBar = document.getElementById('progress-bar');
    const progressText = document.getElementById('progress-text');
    const progressMessage = document.getElementById('progress-message');
    
    // Show progress container
    progressDiv.style.display = 'block';
    
    // Alternative: Use fetch with streaming (better for POST requests)
    fetch('/environments/api/create-stream', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(data)
    }).then(response => {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        
        function processStream() {
            reader.read().then(({ done, value }) => {
                if (done) return;
                
                const chunk = decoder.decode(value);
                const lines = chunk.split('\n');
                
                lines.forEach(line => {
                    if (line.startsWith('data: ')) {
                        const data = JSON.parse(line.substring(6));
                        
                        // Update UI
                        progressBar.style.width = data.progress + '%';
                        progressBar.setAttribute('aria-valuenow', data.progress);
                        progressText.textContent = data.progress + '%';
                        progressMessage.textContent = data.message;
                        
                        // Add step indicator
                        const stepIndicator = document.getElementById('step-' + data.step.toLowerCase().replace(' ', '-'));
                        if (stepIndicator) {
                            stepIndicator.classList.add('active');
                        }
                        
                        if (data.complete) {
                            // Success - redirect or update UI
                            progressMessage.classList.add('text-success');
                            setTimeout(() => {
                                window.location.href = `/environments/editor/${data.env_id}`;
                            }, 1500);
                        } else if (data.error) {
                            // Error - show message
                            progressMessage.classList.add('text-danger');
                            progressBar.classList.add('bg-danger');
                        }
                    }
                });
                
                // Continue reading
                processStream();
            });
        }
        
        processStream();
    }).catch(error => {
        console.error('Stream error:', error);
        progressMessage.textContent = 'Connection error: ' + error.message;
        progressMessage.classList.add('text-danger');
    });
}

