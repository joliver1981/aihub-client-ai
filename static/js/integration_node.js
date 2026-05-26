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
                <label class="form-label fw-bold d-flex align-items-center" style="gap:8px;">
                    <i class="bi bi-gear me-1"></i>Operation <span class="text-danger">*</span>
                    <button type="button" class="btn btn-sm btn-outline-secondary ml-auto"
                            onclick="refreshIntegrationOperations()" title="Re-fetch operations from the server (after template reload)"
                            style="font-size:0.75rem;padding:2px 8px;margin-left:auto;">
                        <i class="bi bi-arrow-clockwise"></i> Refresh
                    </button>
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
async function loadIntegrationOperationsForWorkflow(integrationId, forceRefresh) {
    try {
        // 60s TTL — short enough that template reloads on the server show up
        // quickly without keeping stale cache for the entire page session.
        const cacheEntry = integrationCache.operations[integrationId];
        const CACHE_TTL_MS = 60000;
        if (!forceRefresh && cacheEntry &&
            cacheEntry.fetchedAt &&
            (Date.now() - cacheEntry.fetchedAt) < CACHE_TTL_MS) {
            return cacheEntry.ops;
        }

        // Cache-busting query param so we never hit a stale HTTP cache either
        const response = await fetch(
            `/api/integrations/${integrationId}/operations?_=${Date.now()}`
        );
        const data = await response.json();

        if (data.status === 'success') {
            integrationCache.operations[integrationId] = {
                ops: data.operations,
                fetchedAt: Date.now()
            };
            return data.operations;
        }
        return [];
    } catch (error) {
        console.error('Error loading operations:', error);
        return [];
    }
}

/**
 * Public helper: force-refresh the operations for the currently-selected
 * integration. Called by the small refresh button next to the Operation
 * dropdown (added below).
 */
window.refreshIntegrationOperations = async function() {
    const sel = document.getElementById('integration-selector');
    if (!sel || !sel.value) return;
    // Invalidate so onIntegrationSelectionChange re-fetches
    delete integrationCache.operations[sel.value];
    await onIntegrationSelectionChange();
};

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

    // Group by category — use actual category names from the template
    const categoryIcons = {
        'read': '📖', 'write': '✏️', 'Sites': '🌐', 'Libraries': '📚',
        'Files': '📁', 'Workflow': '⚙️', 'Knowledge': '🎓'
    };
    const grouped = {};
    operations.forEach(op => {
        const cat = op.category || 'Other';
        if (!grouped[cat]) grouped[cat] = [];
        grouped[cat].push(op);
    });

    // Render each category as an optgroup
    Object.keys(grouped).forEach(cat => {
        const ops = grouped[cat];
        const group = document.createElement('optgroup');
        const icon = categoryIcons[cat] || '🔧';
        group.label = `${icon} ${cat}`;
        ops.forEach(op => {
            const option = document.createElement('option');
            option.value = op.key;
            option.textContent = op.name;
            option.dataset.params = JSON.stringify(op.parameters || []);
            option.dataset.description = op.description || '';
            group.appendChild(option);
        });
        operationSelector.appendChild(group);
    });
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
 * Read the currently-selected integration's template_key (set by
 * initIntegrationConfigPanel as a dataset attribute on each option).
 * Returns '' if no integration is selected.
 */
function currentIntegrationTemplateKey() {
    const sel = document.getElementById('integration-selector');
    if (!sel) return '';
    const opt = sel.options[sel.selectedIndex];
    return opt ? (opt.dataset.templateKey || '') : '';
}

/**
 * Decide which integration-specific UI helpers should be wired onto a
 * given parameter input. Keyed by template_key + param name.
 *
 * Returns an object describing what to render:
 *   { type: 'sp_drive' | 'sp_folder' | 'sp_date' | 'sp_pattern' | null,
 *     ... extras }
 */
function integrationParamHelper(param) {
    const tk = currentIntegrationTemplateKey();
    const isSharePoint = (tk === 'sharepoint_online' || tk === 'sharepoint_online_app');
    if (!isSharePoint) return null;

    switch (param.name) {
        case 'drive_id':       return { type: 'sp_drive' };
        case 'folder_path':    return { type: 'sp_folder' };
        case 'file_path':      return { type: 'sp_folder', fileMode: true };
        case 'modified_after': return { type: 'sp_date' };
        case 'file_pattern':   return { type: 'sp_pattern' };
        default:               return null;
    }
}

/**
 * Build parameter input fields for the selected operation
 */
function buildIntegrationParameterInputs(parameters, savedValues) {
    const container = document.getElementById('integration-parameters-container');

    // The generic workflow saveNodeConfig() reads every input by `input.name`
    // and stores the raw value — so the hidden `integration-parameters-json`
    // field gets persisted as a JSON STRING, not a parsed object. Make sure
    // we always work with an object below.
    if (typeof savedValues === 'string') {
        try {
            savedValues = JSON.parse(savedValues || '{}');
        } catch (e) {
            console.warn('[Integration Node] Could not parse saved parameters JSON, starting empty:', e);
            savedValues = {};
        }
    }
    if (!savedValues || typeof savedValues !== 'object') {
        savedValues = {};
    }

    if (!parameters || parameters.length === 0) {
        container.innerHTML = '<p class="text-muted small mb-0"><i class="bi bi-info-circle"></i> No parameters required for this operation</p>';
        return;
    }

    let html = '<label class="form-label fw-bold"><i class="bi bi-sliders me-1"></i>Parameters</label>';

    parameters.forEach(param => {
        const savedValue = savedValues[param.name] || param.default || '';
        const required = param.required ? '<span class="text-danger">*</span>' : '';
        const paramId = `integration-param-${param.name}`;
        const helper = integrationParamHelper(param);

        html += `<div class="mb-2">`;
        html += `<label class="form-label small mb-1">${param.label || param.name} ${required}</label>`;

        // Integration-specific overrides take precedence over the generic
        // type-based switch below. They only fire when the integration is
        // SharePoint AND the param name is one we know how to enhance.
        if (helper && helper.type === 'sp_date') {
            // Render modified_after as a real HTML5 date picker
            html += `<input type="date" class="form-control form-control-sm integration-param"
                            id="${paramId}" data-param="${param.name}" value="${savedValue}">`;
        } else if (helper && helper.type === 'sp_pattern') {
            // file_pattern: text input + datalist of common glob patterns
            html += `<div class="input-group input-group-sm">`;
            html += `<input type="text" class="form-control integration-param"
                            id="${paramId}" data-param="${param.name}"
                            value="${savedValue}" list="sp-file-pattern-suggestions"
                            placeholder="${param.placeholder || '*.pdf'}">`;
            html += `<button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)" title="Insert workflow variable">
                        <i class="bi bi-braces"></i>
                     </button>`;
            html += `</div>`;
            html += `<datalist id="sp-file-pattern-suggestions">
                        <option value="*"></option>
                        <option value="*.pdf"></option>
                        <option value="*.docx"></option>
                        <option value="*.xlsx"></option>
                        <option value="*.csv"></option>
                        <option value="*.txt"></option>
                        <option value="Report_*"></option>
                    </datalist>`;
        } else if (helper && helper.type === 'sp_drive') {
            // drive_id: text input + Browse button (site → library picker)
            html += `<div class="input-group input-group-sm">`;
            html += `<input type="text" class="form-control integration-param"
                            id="${paramId}" data-param="${param.name}"
                            value="${savedValue}" placeholder="Click Browse to pick a library">`;
            html += `<button type="button" class="btn btn-outline-primary"
                            onclick="openSharePointDrivePicker('${paramId}')" title="Browse SharePoint sites and libraries">
                        <i class="bi bi-folder2-open"></i> Browse
                     </button>`;
            html += `<button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)" title="Insert workflow variable">
                        <i class="bi bi-braces"></i>
                     </button>`;
            html += `</div>`;
        } else if (helper && helper.type === 'sp_folder') {
            // folder_path / file_path: text input + Browse button
            // Needs drive_id to be set first — picker will look up the drive_id field by id.
            html += `<div class="input-group input-group-sm">`;
            html += `<input type="text" class="form-control integration-param"
                            id="${paramId}" data-param="${param.name}"
                            value="${savedValue}" placeholder="${helper.fileMode ? 'Folder/File path, e.g. Reports/Q3.pdf' : 'Folder path, e.g. Shared Documents/Inbox'}">`;
            html += `<button type="button" class="btn btn-outline-primary"
                            onclick="openSharePointFolderPicker('integration-param-drive_id', '${paramId}', ${helper.fileMode ? 'true' : 'false'})"
                            title="Browse folders (requires Drive ID to be set first)">
                        <i class="bi bi-folder2-open"></i> Browse
                     </button>`;
            html += `<button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)" title="Insert workflow variable">
                        <i class="bi bi-braces"></i>
                     </button>`;
            html += `</div>`;
        } else {
            // Generic type-based rendering (existing behavior)
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
        // Capture template_key so per-integration UI helpers (e.g. SharePoint
        // browse buttons) know which integration is selected
        option.dataset.templateKey = integ.template_key || '';
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

    // Defensive JSON parser: tolerates the historical "[object Object]" garbage
    // that the generic node-restore loop in workflow.js used to write into
    // hidden fields. If we hit a non-JSON value, just default to {}.
    function safeParseObject(raw) {
        if (!raw) return {};
        if (typeof raw === 'object') return raw;  // already parsed by caller
        const trimmed = String(raw).trim();
        if (!trimmed || trimmed === '[object Object]') return {};
        try {
            return JSON.parse(trimmed);
        } catch (e) {
            console.warn('collectIntegrationNodeConfig: could not parse hidden field, defaulting to {}:', raw);
            return {};
        }
    }

    return {
        integrationId: document.getElementById('integration-selector')?.value || '',
        operation: document.getElementById('integration-operation-selector')?.value || '',
        parameters: safeParseObject(document.getElementById('integration-parameters-json')?.value),
        outputVariable: document.getElementById('integration-output-var')?.value || '',
        continueOnError: document.getElementById('integration-continue-on-error')?.checked || false,
        operationMeta: safeParseObject(document.getElementById('integration-operation-meta')?.value)
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

// ============================================================================
// SharePoint pickers — invoked from the Browse buttons on drive_id / folder_path
// inputs in the integration node config panel. Both reuse the existing
// /api/integrations/<id>/sharepoint/browse endpoint.
// ============================================================================

(function() {
    // The workflow page doesn't ship jQuery or Bootstrap's modal JS, so we
    // build our own fixed-position overlay with vanilla DOM. This also
    // sidesteps the nested-Bootstrap-modal z-index headache entirely —
    // our overlay just sits at z-index 10500 above everything.

    function ensureSpPickerModal() {
        if (document.getElementById('spWorkflowPickerOverlay')) return;
        const overlayHtml = `
        <div id="spWorkflowPickerOverlay" role="dialog" aria-modal="true"
             style="display:none;position:fixed;inset:0;z-index:10500;background:rgba(0,0,0,0.55);
                    align-items:center;justify-content:center;">
            <div style="width:min(720px, 92vw);max-height:90vh;display:flex;flex-direction:column;
                        background:var(--bg-card, #ffffff);color:var(--text-primary, #0f172a);
                        border:1px solid var(--border-color, #d0d7de);
                        border-radius:8px;box-shadow:0 12px 32px rgba(0,0,0,0.35);">
                <div style="display:flex;align-items:center;justify-content:space-between;
                            padding:12px 18px;border-bottom:1px solid var(--border-color, #dee2e6);">
                    <h5 id="spWorkflowPickerTitle" style="margin:0;color:var(--text-primary, #0f172a);">SharePoint Picker</h5>
                    <button type="button" id="spWorkflowPickerCloseBtn" aria-label="Close"
                            style="background:none;border:0;font-size:24px;line-height:1;cursor:pointer;
                                   color:var(--text-primary, #0f172a);padding:0 4px;">&times;</button>
                </div>
                <div style="padding:16px;overflow-y:auto;flex:1;">
                    <div id="spWorkflowPickerStatus"
                         style="display:none;margin-bottom:10px;padding:8px 12px;border-radius:4px;font-size:0.85rem;"></div>
                    <div id="spWorkflowPickerBody"></div>
                </div>
                <div style="padding:10px 18px;border-top:1px solid var(--border-color, #dee2e6);
                            display:flex;justify-content:flex-end;gap:8px;">
                    <button type="button" id="spWorkflowPickerCancelBtn" class="btn btn-secondary btn-sm">Cancel</button>
                    <button type="button" id="spWorkflowPickerSelectBtn" class="btn btn-primary btn-sm" style="display:none;">
                        <i class="bi bi-check-circle"></i> <span id="spWorkflowPickerSelectLabel">Select</span>
                    </button>
                </div>
            </div>
        </div>`;
        const wrap = document.createElement('div');
        wrap.innerHTML = overlayHtml;
        document.body.appendChild(wrap.firstElementChild);

        // Close handlers
        document.getElementById('spWorkflowPickerCloseBtn').onclick = hideSpPickerModal;
        document.getElementById('spWorkflowPickerCancelBtn').onclick = hideSpPickerModal;
        // Click outside the inner card closes the picker
        document.getElementById('spWorkflowPickerOverlay').addEventListener('click', function(e) {
            if (e.target.id === 'spWorkflowPickerOverlay') hideSpPickerModal();
        });
        // Escape key closes
        document.addEventListener('keydown', function(e) {
            const overlay = document.getElementById('spWorkflowPickerOverlay');
            if (overlay && overlay.style.display === 'flex' && e.key === 'Escape') {
                hideSpPickerModal();
            }
        });

        // Stop Bootstrap's modal `enforceFocus` from yanking focus away from
        // inputs inside this overlay. When this picker is invoked from a
        // workflow node config (which lives inside a Bootstrap modal), Bootstrap
        // installs a document-level `focusin` listener that re-focuses the
        // modal-dialog whenever focus lands outside its DOM subtree. Since this
        // overlay is appended to <body> (not inside the modal), every focus
        // attempt on an input here gets stolen back instantly — making the URL
        // field appear "frozen" (no caret, no typing).
        //
        // Z-index alone doesn't solve this; the focus trap is a DOM-containment
        // check, not a layering check. Stopping focusin propagation at the
        // overlay boundary keeps Bootstrap's listener out of the loop.
        document.getElementById('spWorkflowPickerOverlay').addEventListener(
            'focusin',
            function(e) { e.stopPropagation(); },
            true
        );
    }

    function showSpPickerModal() {
        const o = document.getElementById('spWorkflowPickerOverlay');
        if (o) o.style.display = 'flex';
    }
    function hideSpPickerModal() {
        const o = document.getElementById('spWorkflowPickerOverlay');
        if (o) o.style.display = 'none';
    }

    function setStatus(msg, level) {
        const el = document.getElementById('spWorkflowPickerStatus');
        if (!el) return;
        if (!msg) { el.style.display = 'none'; return; }
        // Theme-aware status box (we're not using Bootstrap alert classes here
        // because Bootstrap's alert styles don't always cooperate with the
        // workflow page's dark theme — use semantic background colors instead).
        const palette = {
            info:    { bg: 'rgba(74,144,217,0.15)', fg: '#4a90d9' },
            success: { bg: 'rgba(40,167,69,0.15)',  fg: '#28a745' },
            warning: { bg: 'rgba(255,193,7,0.15)',  fg: '#856404' },
            danger:  { bg: 'rgba(220,53,69,0.15)',  fg: '#dc3545' },
        };
        const c = palette[level || 'info'] || palette.info;
        el.style.background = c.bg;
        el.style.color = c.fg;
        el.style.border = '1px solid ' + c.fg;
        el.textContent = msg;
        el.style.display = '';
    }

    function escHtml(s) {
        const div = document.createElement('div');
        div.textContent = s == null ? '' : String(s);
        return div.innerHTML;
    }

    function spBrowse(integrationId, pathType, extra) {
        const payload = Object.assign({ path_type: pathType }, extra || {});
        return fetch(`/api/integrations/${integrationId}/sharepoint/browse`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        }).then(r => r.json());
    }

    // -----------------------------------------------------------------------
    // DRIVE PICKER (site -> library -> returns drive_id)
    // -----------------------------------------------------------------------
    window.openSharePointDrivePicker = async function(targetFieldId) {
        console.log('[SP Picker] openSharePointDrivePicker called, target =', targetFieldId);
        try {
            ensureSpPickerModal();
        } catch (e) {
            console.error('[SP Picker] ensureSpPickerModal failed:', e);
            alert('Could not build SharePoint picker modal: ' + e.message);
            return;
        }
        const integrationSel = document.getElementById('integration-selector');
        const integrationId = integrationSel ? integrationSel.value : '';
        if (!integrationId) {
            alert('Pick an integration first.');
            return;
        }
        console.log('[SP Picker] integrationId =', integrationId);

        document.getElementById('spWorkflowPickerTitle').textContent = 'Pick a SharePoint Library';
        document.getElementById('spWorkflowPickerSelectBtn').style.display = 'none';

        const body = document.getElementById('spWorkflowPickerBody');
        body.innerHTML = `
            <div class="form-group mb-2">
                <label class="mb-1" style="font-size:0.85rem;font-weight:600;">Site</label>
                <select class="form-control form-control-sm" id="spwSite"><option value="">Loading sites...</option></select>
                <small class="text-muted d-block mt-1">Don't see your site? <a href="#" id="spwToggleUrl">Look up by URL</a></small>
            </div>
            <div class="form-group mb-2" id="spwUrlRow" style="display:none;">
                <div class="input-group input-group-sm">
                    <input type="text" class="form-control" id="spwSiteUrl" placeholder="https://tenant.sharepoint.com/sites/MySite">
                    <div class="input-group-append">
                        <button class="btn btn-outline-primary" id="spwSiteUrlBtn">Resolve</button>
                    </div>
                </div>
            </div>
            <div class="form-group mb-2" id="spwDriveRow" style="display:none;">
                <label class="mb-1" style="font-size:0.85rem;font-weight:600;">Library (Drive)</label>
                <select class="form-control form-control-sm" id="spwDrive"><option value="">Pick a library...</option></select>
            </div>`;

        setStatus(null);
        showSpPickerModal();

        // Load sites
        spBrowse(integrationId, 'sites', { query: '*' }).then(resp => {
            const sel = document.getElementById('spwSite');
            if (resp.status !== 'success' || !resp.data) {
                sel.innerHTML = '<option value="">Could not load sites</option>';
                setStatus(resp.error || 'Failed to list sites', 'danger');
                return;
            }
            const sites = resp.data.sites || [];
            sel.innerHTML = `<option value="">-- Select a site (${sites.length}) --</option>`;
            sites.forEach(s => {
                sel.innerHTML += `<option value="${escHtml(s.id)}">${escHtml(s.name)}${s.hostname ? ' (' + escHtml(s.hostname) + ')' : ''}</option>`;
            });
        });

        document.getElementById('spwToggleUrl').onclick = function(e) {
            e.preventDefault();
            const r = document.getElementById('spwUrlRow');
            r.style.display = r.style.display === 'none' ? '' : 'none';
        };

        document.getElementById('spwSiteUrlBtn').onclick = function() {
            const url = (document.getElementById('spwSiteUrl').value || '').trim();
            if (!url) return;
            setStatus('Resolving URL...', 'info');
            spBrowse(integrationId, 'site_by_url', { url: url }).then(resp => {
                if (resp.status === 'success' && resp.data && resp.data.site) {
                    const site = resp.data.site;
                    const sel = document.getElementById('spwSite');
                    if (!sel.querySelector(`option[value="${site.id}"]`)) {
                        sel.innerHTML += `<option value="${escHtml(site.id)}">${escHtml(site.name)}${site.hostname ? ' (' + escHtml(site.hostname) + ')' : ''}</option>`;
                    }
                    sel.value = site.id;
                    sel.dispatchEvent(new Event('change'));
                    setStatus('Resolved: ' + (site.name || ''), 'success');
                } else {
                    setStatus('Could not resolve URL: ' + (resp.error || 'not found'), 'danger');
                }
            });
        };

        document.getElementById('spwSite').onchange = function() {
            const siteId = this.value;
            if (!siteId) {
                document.getElementById('spwDriveRow').style.display = 'none';
                return;
            }
            const driveSel = document.getElementById('spwDrive');
            driveSel.innerHTML = '<option value="">Loading libraries...</option>';
            document.getElementById('spwDriveRow').style.display = '';
            spBrowse(integrationId, 'drives', { site_id: siteId }).then(resp => {
                if (resp.status === 'success' && resp.data) {
                    const drives = resp.data.drives || [];
                    driveSel.innerHTML = `<option value="">-- Pick a library (${drives.length}) --</option>`;
                    drives.forEach(d => {
                        driveSel.innerHTML += `<option value="${escHtml(d.id)}">${escHtml(d.name)}</option>`;
                    });
                } else {
                    driveSel.innerHTML = '<option value="">Error</option>';
                    setStatus(resp.error || 'Failed to list libraries', 'danger');
                }
            });
        };

        document.getElementById('spwDrive').onchange = function() {
            const btn = document.getElementById('spWorkflowPickerSelectBtn');
            document.getElementById('spWorkflowPickerSelectLabel').textContent = 'Use this library';
            btn.style.display = this.value ? '' : 'none';
            btn.onclick = function() {
                const driveId = document.getElementById('spwDrive').value;
                if (!driveId) return;
                const target = document.getElementById(targetFieldId);
                if (target) {
                    target.value = driveId;
                    target.dispatchEvent(new Event('input', { bubbles: true }));
                    target.dispatchEvent(new Event('change', { bubbles: true }));
                }
                hideSpPickerModal();
            };
        };
    };

    // -----------------------------------------------------------------------
    // FOLDER / FILE PICKER (uses drive_id from another field, navigates by path)
    // -----------------------------------------------------------------------
    window.openSharePointFolderPicker = async function(driveFieldId, targetFieldId, fileMode) {
        console.log('[SP Picker] openSharePointFolderPicker, driveField =', driveFieldId,
                    'target =', targetFieldId, 'fileMode =', fileMode);
        try {
            ensureSpPickerModal();
        } catch (e) {
            console.error('[SP Picker] ensureSpPickerModal failed:', e);
            alert('Could not build picker modal: ' + e.message);
            return;
        }
        const integrationSel = document.getElementById('integration-selector');
        const integrationId = integrationSel ? integrationSel.value : '';
        if (!integrationId) {
            alert('Pick an integration first.');
            return;
        }
        const driveField = document.getElementById(driveFieldId);
        const driveId = driveField ? (driveField.value || '').trim() : '';
        if (!driveId) {
            alert('Set the Drive ID first — click Browse next to the Drive ID field.');
            return;
        }
        console.log('[SP Picker] integrationId =', integrationId, 'driveId =', driveId);

        document.getElementById('spWorkflowPickerTitle').textContent =
            fileMode ? 'Pick a SharePoint File' : 'Pick a SharePoint Folder';
        document.getElementById('spWorkflowPickerSelectLabel').textContent =
            fileMode ? 'Use this file' : 'Use this folder';
        document.getElementById('spWorkflowPickerSelectBtn').style.display = 'none';

        // State for navigation
        const state = { stack: [] }; // [{id, name}]

        const body = document.getElementById('spWorkflowPickerBody');
        body.innerHTML = `
            <div id="spwBreadcrumb" class="mb-2" style="font-size:0.85rem;"></div>
            <div id="spwFolderList" style="max-height:380px;overflow-y:auto;border:1px solid #dee2e6;border-radius:4px;"></div>
            <div class="mt-2" id="spwSelectionRow" style="font-size:0.85rem;color:#0078d4;"></div>`;
        setStatus(null);

        function pathFromStack() {
            return state.stack.map(s => s.name).join('/');
        }

        function renderBreadcrumb() {
            const bc = document.getElementById('spwBreadcrumb');
            let parts = [`<a href="#" onclick="return spwGoTo(-1)"><i class="bi bi-house"></i> Drive root</a>`];
            state.stack.forEach((s, idx) => {
                parts.push('<span class="mx-1 text-muted">/</span>');
                if (idx === state.stack.length - 1) {
                    parts.push(`<span>${escHtml(s.name)}</span>`);
                } else {
                    parts.push(`<a href="#" onclick="return spwGoTo(${idx})">${escHtml(s.name)}</a>`);
                }
            });
            bc.innerHTML = parts.join('');
        }

        function renderSelectionRow() {
            const row = document.getElementById('spwSelectionRow');
            const path = pathFromStack();
            if (fileMode) {
                row.textContent = '';
            } else if (path) {
                row.textContent = 'Will select folder: /' + path;
            } else {
                row.textContent = 'At drive root — click "Use this folder" to select root, or open a folder.';
            }
        }

        function pickFolder(folderId, folderName) {
            state.stack.push({ id: folderId, name: folderName });
            loadCurrent();
        }

        window.spwGoTo = function(idx) {
            if (idx < 0) state.stack = [];
            else state.stack = state.stack.slice(0, idx + 1);
            loadCurrent();
            return false;
        };

        function loadCurrent() {
            renderBreadcrumb();
            renderSelectionRow();
            const list = document.getElementById('spwFolderList');
            list.innerHTML = '<div class="text-muted p-3" style="font-size:0.85rem;">Loading...</div>';

            const top = state.stack[state.stack.length - 1];
            const payload = {
                drive_id: driveId,
                item_id: top ? top.id : '',
                top: 200
            };
            spBrowse(integrationId, 'items', payload).then(resp => {
                if (resp.status !== 'success' || !resp.data) {
                    list.innerHTML = '<div class="text-danger p-3" style="font-size:0.85rem;">' + escHtml(resp.error || 'Failed to list') + '</div>';
                    return;
                }
                const items = resp.data.items || [];
                if (!items.length) {
                    list.innerHTML = '<div class="text-muted p-3" style="font-size:0.85rem;">Empty</div>';
                } else {
                    let html = '<table class="table table-sm table-hover mb-0" style="font-size:0.85rem;"><tbody>';
                    // Sort: folders first
                    items.sort((a, b) => {
                        if (a.type !== b.type) return a.type === 'folder' ? -1 : 1;
                        return (a.name || '').localeCompare(b.name || '');
                    });
                    items.forEach(item => {
                        if (item.type === 'folder') {
                            html += `<tr>
                                <td style="cursor:pointer;" onclick='spwOpenFolder(${JSON.stringify(item.id)}, ${JSON.stringify(item.name)})'>
                                    <i class="bi bi-folder-fill" style="color:#f0c04e;"></i>
                                    <span style="color:#0078d4;">${escHtml(item.name)}</span>
                                    <span class="text-muted ml-2">(${item.childCount || 0})</span>
                                </td>
                                <td style="width:80px;text-align:right;">
                                    <button class="btn btn-sm btn-outline-success" onclick='spwSelectFolder(${JSON.stringify(item.id)}, ${JSON.stringify(item.name)})'
                                            ${fileMode ? 'style="display:none;"' : ''}>Use</button>
                                </td>
                            </tr>`;
                        } else {
                            html += `<tr>
                                <td>
                                    <i class="bi bi-file-earmark text-muted"></i>
                                    <span>${escHtml(item.name)}</span>
                                    <span class="text-muted ml-2">${item.size ? Math.round(item.size/1024) + ' KB' : ''}</span>
                                </td>
                                <td style="width:80px;text-align:right;">
                                    ${fileMode ? `<button class="btn btn-sm btn-outline-success" onclick='spwSelectFile(${JSON.stringify(item.name)})'>Use</button>` : ''}
                                </td>
                            </tr>`;
                        }
                    });
                    html += '</tbody></table>';
                    list.innerHTML = html;
                }

                // In folder mode, the main Select button picks the current folder
                if (!fileMode) {
                    const btn = document.getElementById('spWorkflowPickerSelectBtn');
                    btn.style.display = '';
                    btn.onclick = function() {
                        finishWith(pathFromStack());
                    };
                }
            });
        }

        window.spwOpenFolder = function(id, name) {
            state.stack.push({ id: id, name: name });
            loadCurrent();
        };

        window.spwSelectFolder = function(id, name) {
            // Inline button on a folder row — select that folder without opening it
            const path = pathFromStack();
            finishWith(path ? path + '/' + name : name);
        };

        window.spwSelectFile = function(name) {
            const path = pathFromStack();
            finishWith(path ? path + '/' + name : name);
        };

        function finishWith(value) {
            const target = document.getElementById(targetFieldId);
            if (target) {
                target.value = value;
                target.dispatchEvent(new Event('input', { bubbles: true }));
                target.dispatchEvent(new Event('change', { bubbles: true }));
            }
            hideSpPickerModal();
        }

        loadCurrent();
        showSpPickerModal();
    };

    // Final confirmation that the public picker functions are wired up.
    // If you don't see this in DevTools console, the IIFE didn't finish —
    // check the line(s) above for a runtime error.
    console.log(
        '[SP Picker] Pickers defined:',
        'openSharePointDrivePicker=' + (typeof window.openSharePointDrivePicker),
        '/ openSharePointFolderPicker=' + (typeof window.openSharePointFolderPicker)
    );
})();
