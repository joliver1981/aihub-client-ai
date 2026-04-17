// integration_node_fixed.js
// Integration Node support for Workflow Designer
// Add this to the bottom of workflow.js or include after workflow.js loads

// ============================================
// Integration Node Configuration Template
// ============================================
nodeConfigTemplates['Integration'] = {
    template: `
        <div class="integration-config">
            <!-- Integration Selector -->
            <div class="mb-3">
                <label class="form-label fw-bold">
                    <i class="bi bi-plug me-1"></i>Select Integration <span class="text-danger">*</span>
                </label>
                <select class="form-control" name="integrationId" id="integration-selector" 
                        onchange="onIntegrationSelectionChange()">
                    <option value="">Loading integrations...</option>
                </select>
                <small class="form-text text-muted">
                    <a href="/integrations" target="_blank">
                        <i class="bi bi-plus-circle"></i> Connect new integration
                    </a>
                </small>
            </div>
            
            <!-- Operation Selector -->
            <div class="mb-3">
                <label class="form-label fw-bold">
                    <i class="bi bi-gear me-1"></i>Operation <span class="text-danger">*</span>
                </label>
                <select class="form-control" name="operation" id="integration-operation-selector" disabled
                        onchange="onIntegrationOperationChange()">
                    <option value="">Select an integration first...</option>
                </select>
            </div>
            
            <!-- Dynamic Parameters Container -->
            <div id="integration-parameters-container" class="mb-3">
                <!-- Parameters will be loaded dynamically based on selected operation -->
            </div>
            
            <hr class="my-3">
            
            <!-- Output Configuration -->
            <div class="mb-3">
                <label class="form-label fw-bold">
                    <i class="bi bi-box-arrow-right me-1"></i>Output Variable
                </label>
                <div class="input-group">
                    <input type="text" class="form-control" name="outputVariable" id="integration-output-var"
                           placeholder="apiResult" list="integration-output-var-list">
                    <datalist id="integration-output-var-list"></datalist>
                </div>
                <small class="form-text text-muted">Store the operation result in this variable</small>
            </div>
            
            <!-- Error Handling -->
            <div class="mb-3">
                <div class="form-check">
                    <input type="checkbox" class="form-check-input" name="continueOnError" id="integration-continue-on-error">
                    <label class="form-check-label" for="integration-continue-on-error">
                        <strong>Continue workflow on error</strong>
                    </label>
                </div>
                <small class="form-text text-muted">If unchecked, workflow will stop if this operation fails</small>
            </div>
            
            <!-- Hidden field to store parameters as JSON -->
            <input type="hidden" name="parameters" id="integration-parameters-json" value="{}">
            
            <!-- Hidden field to store operation metadata -->
            <input type="hidden" name="operationMeta" id="integration-operation-meta" value="{}">
        </div>
    `,
    defaultConfig: {
        integrationId: '',
        operation: '',
        parameters: {},
        outputVariable: '',
        continueOnError: false,
        operationMeta: {}
    }
};

// ============================================
// Integration Node Helper Functions
// ============================================

// Cache for integrations and operations
let integrationCache = {
    integrations: null,
    operations: {},
    lastFetch: null
};

/**
 * Load integrations and populate the selector
 */
async function loadIntegrationsForWorkflow() {
    try {
        // Check cache (refresh every 5 minutes)
        if (integrationCache.integrations && 
            integrationCache.lastFetch && 
            (Date.now() - integrationCache.lastFetch) < 300000) {
            return integrationCache.integrations;
        }
        
        const response = await fetch('/api/integrations');
        const data = await response.json();
        
        if (data.status === 'success') {
            integrationCache.integrations = data.integrations;
            integrationCache.lastFetch = Date.now();
            return data.integrations;
        }
        return [];
    } catch (error) {
        console.error('Error loading integrations:', error);
        return [];
    }
}

/**
 * Load operations for a specific integration
 */
async function loadIntegrationOperationsForWorkflow(integrationId) {
    try {
        // Check cache
        if (integrationCache.operations[integrationId]) {
            return integrationCache.operations[integrationId];
        }
        
        const response = await fetch(`/api/integrations/${integrationId}/operations`);
        const data = await response.json();
        
        if (data.status === 'success') {
            integrationCache.operations[integrationId] = data.operations;
            return data.operations;
        }
        return [];
    } catch (error) {
        console.error('Error loading operations:', error);
        return [];
    }
}

/**
 * Handle integration selection change
 */
async function onIntegrationSelectionChange() {
    const integrationSelector = document.getElementById('integration-selector');
    const operationSelector = document.getElementById('integration-operation-selector');
    const paramsContainer = document.getElementById('integration-parameters-container');
    
    const integrationId = integrationSelector.value;
    
    // Clear operation selector and parameters
    operationSelector.innerHTML = '<option value="">Loading operations...</option>';
    operationSelector.disabled = true;
    paramsContainer.innerHTML = '';
    document.getElementById('integration-parameters-json').value = '{}';
    
    if (!integrationId) {
        operationSelector.innerHTML = '<option value="">Select an integration first...</option>';
        return;
    }
    
    // Load operations for this integration
    const operations = await loadIntegrationOperationsForWorkflow(integrationId);
    
    operationSelector.innerHTML = '<option value="">Select an operation...</option>';
    operationSelector.disabled = false;
    
    // Group by category
    const readOps = operations.filter(op => op.category === 'read');
    const writeOps = operations.filter(op => op.category === 'write');
    const otherOps = operations.filter(op => !['read', 'write'].includes(op.category));
    
    if (readOps.length > 0) {
        const group = document.createElement('optgroup');
        group.label = '📖 Read Operations';
        readOps.forEach(op => {
            const option = document.createElement('option');
            option.value = op.key;
            option.textContent = op.name;
            option.dataset.params = JSON.stringify(op.parameters || []);
            option.dataset.description = op.description || '';
            group.appendChild(option);
        });
        operationSelector.appendChild(group);
    }
    
    if (writeOps.length > 0) {
        const group = document.createElement('optgroup');
        group.label = '✏️ Write Operations';
        writeOps.forEach(op => {
            const option = document.createElement('option');
            option.value = op.key;
            option.textContent = op.name;
            option.dataset.params = JSON.stringify(op.parameters || []);
            option.dataset.description = op.description || '';
            group.appendChild(option);
        });
        operationSelector.appendChild(group);
    }
    
    if (otherOps.length > 0) {
        const group = document.createElement('optgroup');
        group.label = '🔧 Other Operations';
        otherOps.forEach(op => {
            const option = document.createElement('option');
            option.value = op.key;
            option.textContent = op.name;
            option.dataset.params = JSON.stringify(op.parameters || []);
            option.dataset.description = op.description || '';
            group.appendChild(option);
        });
        operationSelector.appendChild(group);
    }
}

/**
 * Handle operation selection change
 */
function onIntegrationOperationChange() {
    const operationSelector = document.getElementById('integration-operation-selector');
    const paramsContainer = document.getElementById('integration-parameters-container');
    
    const selectedOption = operationSelector.options[operationSelector.selectedIndex];
    
    if (!selectedOption || !selectedOption.value) {
        paramsContainer.innerHTML = '';
        document.getElementById('integration-parameters-json').value = '{}';
        return;
    }
    
    // Get parameter definitions from the selected option
    const params = JSON.parse(selectedOption.dataset.params || '[]');
    const description = selectedOption.dataset.description || '';
    
    // Store operation metadata
    document.getElementById('integration-operation-meta').value = JSON.stringify({
        key: selectedOption.value,
        name: selectedOption.textContent,
        description: description
    });
    
    // Build parameter inputs
    buildIntegrationParameterInputs(params, {});
}

/**
 * Build parameter input fields for the selected operation
 */
function buildIntegrationParameterInputs(parameters, savedValues) {
    const container = document.getElementById('integration-parameters-container');
    
    if (!parameters || parameters.length === 0) {
        container.innerHTML = '<p class="text-muted small mb-0"><i class="bi bi-info-circle"></i> No parameters required for this operation</p>';
        return;
    }
    
    let html = '<label class="form-label fw-bold"><i class="bi bi-sliders me-1"></i>Parameters</label>';
    
    parameters.forEach(param => {
        const savedValue = savedValues[param.name] || param.default || '';
        const required = param.required ? '<span class="text-danger">*</span>' : '';
        const paramId = `integration-param-${param.name}`;
        
        html += `<div class="mb-2">`;
        html += `<label class="form-label small mb-1">${param.label || param.name} ${required}</label>`;
        
        switch (param.type) {
            case 'select':
                html += `<select class="form-control form-control-sm integration-param" id="${paramId}" data-param="${param.name}">`;
                (param.options || []).forEach(opt => {
                    const optValue = typeof opt === 'object' ? opt.value : opt;
                    const optLabel = typeof opt === 'object' ? opt.label : opt;
                    const selected = optValue === savedValue ? 'selected' : '';
                    html += `<option value="${optValue}" ${selected}>${optLabel}</option>`;
                });
                html += `</select>`;
                break;
                
            case 'textarea':
            case 'json':
                html += `<div class="input-group input-group-sm">`;
                html += `<textarea class="form-control integration-param" id="${paramId}" data-param="${param.name}" 
                                  rows="2" placeholder="${param.placeholder || ''}">${savedValue}</textarea>`;
                html += `<button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                            <i class="bi bi-braces"></i>
                         </button>`;
                html += `</div>`;
                break;
                
            case 'number':
                html += `<div class="input-group input-group-sm">`;
                html += `<input type="number" class="form-control integration-param" id="${paramId}" data-param="${param.name}"
                                value="${savedValue}" placeholder="${param.placeholder || ''}">`;
                html += `<button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                            <i class="bi bi-braces"></i>
                         </button>`;
                html += `</div>`;
                break;
                
            case 'boolean':
            case 'checkbox':
                const checked = savedValue === true || savedValue === 'true' ? 'checked' : '';
                html += `<div class="form-check">
                            <input type="checkbox" class="form-check-input integration-param" 
                                   id="${paramId}" data-param="${param.name}" ${checked}>
                            <label class="form-check-label small" for="${paramId}">Enable</label>
                         </div>`;
                break;
                
            case 'date':
                html += `<input type="date" class="form-control form-control-sm integration-param" id="${paramId}" 
                                data-param="${param.name}" value="${savedValue}">`;
                break;
                
            case 'datetime':
                html += `<input type="datetime-local" class="form-control form-control-sm integration-param" id="${paramId}" 
                                data-param="${param.name}" value="${savedValue}">`;
                break;
                
            default: // text
                html += `<div class="input-group input-group-sm">`;
                html += `<input type="text" class="form-control integration-param" id="${paramId}" data-param="${param.name}"
                                value="${savedValue}" placeholder="${param.placeholder || ''}">`;
                html += `<button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                            <i class="bi bi-braces"></i>
                         </button>`;
                html += `</div>`;
        }
        
        if (param.description) {
            html += `<small class="form-text text-muted">${param.description}</small>`;
        }
        
        html += `</div>`;
    });
    
    container.innerHTML = html;
    
    // Add change listeners to update the hidden JSON field
    container.querySelectorAll('.integration-param').forEach(input => {
        input.addEventListener('change', updateIntegrationParametersJson);
        input.addEventListener('input', updateIntegrationParametersJson);
    });
}

/**
 * Update the hidden parameters JSON field when parameter values change
 */
function updateIntegrationParametersJson() {
    const params = {};
    document.querySelectorAll('.integration-param').forEach(input => {
        const paramName = input.dataset.param;
        if (input.type === 'checkbox') {
            params[paramName] = input.checked;
        } else if (input.value !== '') {
            params[paramName] = input.value;
        }
    });
    document.getElementById('integration-parameters-json').value = JSON.stringify(params);
}

/**
 * Initialize Integration node config panel when opened
 * Called by the workflow designer when the config modal is shown
 */
async function initIntegrationConfigPanel(existingConfig) {
    const integrationSelector = document.getElementById('integration-selector');
    
    // Load integrations
    const integrations = await loadIntegrationsForWorkflow();
    
    integrationSelector.innerHTML = '<option value="">Select an integration...</option>';
    
    integrations.forEach(integ => {
        const option = document.createElement('option');
        option.value = integ.integration_id;
        option.textContent = `${integ.integration_name} (${integ.platform_name})`;
        if (!integ.is_connected) {
            option.textContent += ' (Disconnected)';
            option.disabled = true;
        }
        integrationSelector.appendChild(option);
    });
    
    // If we have existing config, restore it
    if (existingConfig && existingConfig.integrationId) {
        integrationSelector.value = existingConfig.integrationId;
        
        // Trigger load of operations
        await onIntegrationSelectionChange();
        
        // Set the operation
        const operationSelector = document.getElementById('integration-operation-selector');
        if (existingConfig.operation) {
            operationSelector.value = existingConfig.operation;
            
            // Get parameter definitions and build inputs with saved values
            const selectedOption = operationSelector.options[operationSelector.selectedIndex];
            if (selectedOption && selectedOption.value) {
                const params = JSON.parse(selectedOption.dataset.params || '[]');
                buildIntegrationParameterInputs(params, existingConfig.parameters || {});
            }
        }
        
        // Set output variable
        if (existingConfig.outputVariable) {
            document.getElementById('integration-output-var').value = existingConfig.outputVariable;
        }
        
        // Set continue on error
        if (existingConfig.continueOnError) {
            document.getElementById('integration-continue-on-error').checked = true;
        }
    }
    
    // Populate variable datalist for output variable
    populateIntegrationVariableDatalist();
}

/**
 * Populate the datalist with available workflow variables
 */
function populateIntegrationVariableDatalist() {
    const datalist = document.getElementById('integration-output-var-list');
    if (!datalist) return;
    
    datalist.innerHTML = '';
    
    // Get workflow variables if available
    if (typeof workflowVariableDefinitions !== 'undefined') {
        Object.keys(workflowVariableDefinitions).forEach(varName => {
            const option = document.createElement('option');
            option.value = varName;
            datalist.appendChild(option);
        });
    }
}

/**
 * Collect Integration config from the form
 * This is called when saving the node configuration
 */
function collectIntegrationNodeConfig() {
    // Update parameters JSON before collecting
    updateIntegrationParametersJson();
    
    return {
        integrationId: document.getElementById('integration-selector')?.value || '',
        operation: document.getElementById('integration-operation-selector')?.value || '',
        parameters: JSON.parse(document.getElementById('integration-parameters-json')?.value || '{}'),
        outputVariable: document.getElementById('integration-output-var')?.value || '',
        continueOnError: document.getElementById('integration-continue-on-error')?.checked || false,
        operationMeta: JSON.parse(document.getElementById('integration-operation-meta')?.value || '{}')
    };
}

// ============================================
// CSS Styles for Integration Node
// ============================================
const integrationNodeStyles = document.createElement('style');
integrationNodeStyles.textContent = `
    .tool-item[data-type="Integration"] {
        background: linear-gradient(135deg, #6f42c1 0%, #8b5cf6 100%);
        color: white;
        border: none;
    }
    
    .tool-item[data-type="Integration"]:hover {
        background: linear-gradient(135deg, #5a32a3 0%, #7c4fe0 100%);
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(111, 66, 193, 0.3);
    }
    
    .workflow-node[data-type="Integration"] {
        border-color: #6f42c1;
    }
    
    .workflow-node[data-type="Integration"] .node-header {
        background: linear-gradient(135deg, #6f42c1 0%, #8b5cf6 100%);
        color: white;
    }
    
    .integration-config .form-label.fw-bold {
        color: #6f42c1;
    }
    
    #integration-parameters-container {
        background: #f8f9fa;
        border-radius: 4px;
        padding: 10px;
        margin-top: 5px;
    }
    
    #integration-parameters-container:empty {
        display: none;
    }
`;
document.head.appendChild(integrationNodeStyles);

// ============================================
// Hook into workflow designer initialization
// ============================================
// If there's a modal show event, initialize the panel
document.addEventListener('shown.bs.modal', function(event) {
    const modal = event.target;
    if (modal.id === 'nodeConfigModal' || modal.classList.contains('node-config-modal')) {
        // Check if this is an Integration node
        const integrationSelector = modal.querySelector('#integration-selector');
        if (integrationSelector) {
            // Get existing config if editing
            const existingConfig = configuredNode ? nodeConfigs.get(configuredNode.id) : null;
            initIntegrationConfigPanel(existingConfig);
        }
    }
});

console.log('Integration node registered successfully');
