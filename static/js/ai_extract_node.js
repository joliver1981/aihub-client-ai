// ai_extract_node.js
// AI Extract Node - Frontend configuration and interaction

/**
 * AI Extract Node Configuration Template
 * Add this to the nodeConfigTemplates object in workflow.js
 */
const AIExtractNodeTemplate = {
    template: `
        <div class="ai-extract-config">
            <!-- Extraction Type -->
            <div class="mb-3">
                <label class="form-label fw-bold">Extraction Type</label>
                <select class="form-select" name="extractionType" id="ai-extract-type" onchange="AIExtractNode.onExtractionTypeChange(this)">
                    <option value="field_extraction">Field Extraction</option>
                    <!-- Future types will be added here -->
                    <!--
                    <option value="entity_extraction">Entity Extraction</option>
                    <option value="table_extraction">Table Extraction</option>
                    -->
                </select>
                <small class="form-text text-muted">Select the type of data to extract</small>
            </div>
            
            <!-- Input Source -->
            <div class="mb-3">
                <label class="form-label fw-bold">Input Source</label>
                <div class="input-group">
                    <input type="text" class="form-control" name="inputVariable" id="ai-extract-input" 
                           list="ai-extract-variables-list" placeholder="\${document.content}">
                    <datalist id="ai-extract-variables-list">
                        <!-- Will be populated dynamically -->
                    </datalist>
                    <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                        <i class="bi bi-braces"></i>
                    </button>
                </div>
                <small class="form-text text-muted">Variable containing the text to extract from</small>
            </div>
            
            <!-- Fields Section -->
            <div class="mb-3" id="ai-extract-fields-section">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <label class="form-label fw-bold mb-0">Fields to Extract</label>
                    <button type="button" class="btn btn-sm btn-outline-primary" onclick="AIExtractNode.addField()">
                        <i class="bi bi-plus-lg"></i> Add Field
                    </button>
                </div>
                
                <div id="ai-extract-fields-container" class="fields-container">
                    <!-- Fields will be added dynamically -->
                </div>
                
                <div id="ai-extract-no-fields" class="text-muted text-center py-3 border rounded" style="display: none;">
                    <i class="bi bi-info-circle"></i> No fields defined. Click "Add Field" to start.
                </div>
            </div>
            
            <!-- Special Instructions -->
            <div class="mb-3">
                <label class="form-label fw-bold">Special Instructions</label>
                <textarea class="form-control" name="specialInstructions" id="ai-extract-instructions" 
                          rows="3" placeholder="Optional: Add any special instructions for the AI..."></textarea>
                <small class="form-text text-muted">
                    E.g., "Return numbers without currency symbols"
                </small>
            </div>
            
            <!-- Output Configuration -->
            <div class="mb-3">
                <label class="form-label fw-bold">Output Variable</label>
                <div class="input-group">
                    <span class="input-group-text">\${</span>
                    <input type="text" class="form-control" name="outputVariable" id="ai-extract-output" 
                           placeholder="extractedData" value="extractedData">
                    <span class="input-group-text">}</span>
                </div>
                <small class="form-text text-muted">
                    Variable to store extracted data. Access with \${extractedData.fieldName}
                </small>
            </div>
            
            <!-- Options -->
            <div class="mb-3">
                <div class="form-check">
                    <input type="checkbox" class="form-check-input" name="failOnMissingRequired" 
                           id="ai-extract-fail-required">
                    <label class="form-check-label" for="ai-extract-fail-required">
                        Fail node if required fields are not found
                    </label>
                </div>
            </div>
            
            <!-- Output Preview -->
            <div class="mb-3">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <label class="form-label fw-bold mb-0">Output Preview</label>
                    <button type="button" class="btn btn-sm btn-outline-info" onclick="AIExtractNode.testExtraction()">
                        <i class="bi bi-play"></i> Test Extraction
                    </button>
                </div>
                <pre id="ai-extract-preview" class="bg-light border rounded p-2" 
                     style="max-height: 200px; overflow-y: auto; font-size: 0.8rem;">
{
  // Add fields to see preview
}
                </pre>
            </div>
        </div>
    `,
    defaultConfig: {
        extractionType: 'field_extraction',
        inputVariable: '',
        outputVariable: 'extractedData',
        specialInstructions: '',
        failOnMissingRequired: false,
        fields: []
    }
};


/**
 * AI Extract Node Module
 * Handles all frontend logic for the AI Extract node
 */
const AIExtractNode = {
    
    // Current fields being edited
    currentFields: [],
    
    // Field ID counter
    fieldIdCounter: 0,
    
    /**
     * Initialize the AI Extract node configuration panel
     */
    init: function() {
        this.currentFields = [];
        this.fieldIdCounter = 0;
        this.populateVariablesList();
        this.updateFieldsDisplay();
        this.updateOutputPreview();
    },
    
    /**
     * Populate the variables datalist for input source
     */
    populateVariablesList: function() {
        const datalist = document.getElementById('ai-extract-variables-list');
        if (!datalist) return;
        
        datalist.innerHTML = '';
        
        // Get available variables from workflow
        if (typeof workflowVariables !== 'undefined') {
            for (const [name, data] of Object.entries(workflowVariables)) {
                const option = document.createElement('option');
                option.value = `\${${name}}`;
                datalist.appendChild(option);
            }
        }
        
        // Add common variables
        const commonVars = [
            '${document.content}',
            '${_previousStepOutput}',
            '${_previousStepOutput.data}'
        ];
        
        commonVars.forEach(v => {
            const option = document.createElement('option');
            option.value = v;
            datalist.appendChild(option);
        });
    },
    
    /**
     * Handle extraction type change
     */
    onExtractionTypeChange: function(select) {
        const type = select.value;
        // Future: show different UI based on extraction type
        console.log('Extraction type changed to:', type);
    },
    
    /**
     * Add a new field
     */
    addField: function(parentFieldId = null) {
        const fieldId = `field_${this.fieldIdCounter++}`;
        
        const newField = {
            id: fieldId,
            name: '',
            type: 'text',
            required: false,
            description: '',
            children: []
        };
        
        if (parentFieldId) {
            // Add as child of parent field
            const parentField = this.findFieldById(parentFieldId, this.currentFields);
            if (parentField) {
                parentField.children = parentField.children || [];
                parentField.children.push(newField);
            }
        } else {
            // Add to root level
            this.currentFields.push(newField);
        }
        
        this.updateFieldsDisplay();
        this.updateOutputPreview();
        
        // Focus the name input of the new field
        setTimeout(() => {
            const nameInput = document.querySelector(`#${fieldId} .field-name-input`);
            if (nameInput) nameInput.focus();
        }, 100);
    },
    
    /**
     * Remove a field
     */
    removeField: function(fieldId) {
        this.currentFields = this.removeFieldById(fieldId, this.currentFields);
        this.updateFieldsDisplay();
        this.updateOutputPreview();
    },
    
    /**
     * Find a field by ID recursively
     */
    findFieldById: function(fieldId, fields) {
        for (const field of fields) {
            if (field.id === fieldId) return field;
            if (field.children && field.children.length > 0) {
                const found = this.findFieldById(fieldId, field.children);
                if (found) return found;
            }
        }
        return null;
    },
    
    /**
     * Remove a field by ID recursively
     */
    removeFieldById: function(fieldId, fields) {
        return fields.filter(field => {
            if (field.id === fieldId) return false;
            if (field.children) {
                field.children = this.removeFieldById(fieldId, field.children);
            }
            return true;
        });
    },
    
    /**
     * Update field property
     */
    updateField: function(fieldId, property, value) {
        const field = this.findFieldById(fieldId, this.currentFields);
        if (field) {
            field[property] = value;
            
            // If type changed to/from group types, handle children
            if (property === 'type') {
                if (value === 'group' || value === 'repeated_group') {
                    field.children = field.children || [];
                } else {
                    field.children = [];
                }
                this.updateFieldsDisplay();
            }
            
            this.updateOutputPreview();
        }
    },
    
    /**
     * Validate field name
     */
    validateFieldName: function(name, inputElement) {
        const pattern = /^[a-zA-Z_][a-zA-Z0-9_]*$/;
        const isValid = pattern.test(name) || name === '';
        
        if (inputElement) {
            inputElement.classList.toggle('is-invalid', !isValid && name !== '');
            inputElement.classList.toggle('is-valid', isValid && name !== '');
        }
        
        return isValid;
    },
    
    /**
     * Update the fields display
     */
    updateFieldsDisplay: function() {
        const container = document.getElementById('ai-extract-fields-container');
        const noFieldsMsg = document.getElementById('ai-extract-no-fields');
        
        if (!container) return;
        
        if (this.currentFields.length === 0) {
            container.innerHTML = '';
            if (noFieldsMsg) noFieldsMsg.style.display = 'block';
            return;
        }
        
        if (noFieldsMsg) noFieldsMsg.style.display = 'none';
        container.innerHTML = this.renderFields(this.currentFields, 0);
    },
    
    /**
     * Render fields HTML recursively
     */
    renderFields: function(fields, level) {
        let html = '';
        const indent = level * 20;
        
        for (const field of fields) {
            const isGroupType = field.type === 'group' || field.type === 'repeated_group';
            const hasChildren = field.children && field.children.length > 0;
            
            html += `
                <div class="field-item card mb-2" id="${field.id}" style="margin-left: ${indent}px;">
                    <div class="card-body p-2">
                        <!-- Row 1: Name and Type -->
                        <div class="row g-2 mb-2">
                            <div class="col-6">
                                <label class="form-label small mb-1">Field Name</label>
                                <input type="text" class="form-control form-control-sm field-name-input" 
                                    placeholder="field_name" value="${this.escapeHtml(field.name)}"
                                    onchange="AIExtractNode.updateField('${field.id}', 'name', this.value)"
                                    oninput="AIExtractNode.validateFieldName(this.value, this)"
                                    pattern="^[a-zA-Z_][a-zA-Z0-9_]*$"
                                    title="Use only letters, numbers, and underscores">
                            </div>
                            <div class="col-4">
                                <label class="form-label small mb-1">Type</label>
                                <select class="form-select form-select-sm" 
                                        onchange="AIExtractNode.updateField('${field.id}', 'type', this.value)">
                                    <option value="text" ${field.type === 'text' ? 'selected' : ''}>Text</option>
                                    <option value="number" ${field.type === 'number' ? 'selected' : ''}>Number</option>
                                    <option value="boolean" ${field.type === 'boolean' ? 'selected' : ''}>Boolean</option>
                                    <option value="list" ${field.type === 'list' ? 'selected' : ''}>List</option>
                                    <option value="group" ${field.type === 'group' ? 'selected' : ''}>Group</option>
                                    <option value="repeated_group" ${field.type === 'repeated_group' ? 'selected' : ''}>Repeated</option>
                                </select>
                            </div>
                            <div class="col-2 d-flex align-items-end justify-content-end">
                                ${isGroupType ? `
                                    <button type="button" class="btn btn-sm btn-outline-primary me-1" 
                                            onclick="AIExtractNode.addField('${field.id}')" title="Add child field">
                                        <i class="bi bi-plus"></i>
                                    </button>
                                ` : ''}
                                <button type="button" class="btn btn-sm btn-outline-danger" 
                                        onclick="AIExtractNode.removeField('${field.id}')" title="Remove field">
                                    <i class="bi bi-trash"></i>
                                </button>
                            </div>
                        </div>
                        
                        <!-- Row 2: Description and Required -->
                        <div class="row g-2">
                            <div class="col-9">
                                <input type="text" class="form-control form-control-sm" 
                                    placeholder="Description (helps AI understand what to extract)" 
                                    value="${this.escapeHtml(field.description)}"
                                    onchange="AIExtractNode.updateField('${field.id}', 'description', this.value)">
                            </div>
                            <div class="col-3">
                                <div class="form-check form-check-inline mt-1">
                                    <input type="checkbox" class="form-check-input" 
                                        ${field.required ? 'checked' : ''}
                                        onchange="AIExtractNode.updateField('${field.id}', 'required', this.checked)"
                                        id="req-${field.id}">
                                    <label class="form-check-label small" for="req-${field.id}">Required</label>
                                </div>
                            </div>
                        </div>
                        
                        ${isGroupType && hasChildren ? `
                            <div class="children-container mt-2 border-start ps-2">
                                <small class="text-muted d-block mb-1">Child fields:</small>
                                ${this.renderFields(field.children, 0)}
                            </div>
                        ` : ''}
                        
                        ${isGroupType && !hasChildren ? `
                            <div class="text-muted small mt-2">
                                <i class="bi bi-info-circle"></i> Click + to add child fields
                            </div>
                        ` : ''}
                    </div>
                </div>
            `;
        }
        
        return html;
    },
    
    /**
     * Update the output preview
     */
    updateOutputPreview: function() {
        const preview = document.getElementById('ai-extract-preview');
        if (!preview) return;
        
        const schema = this.buildPreviewSchema(this.currentFields);
        preview.textContent = JSON.stringify(schema, null, 2);
    },
    
    /**
     * Build preview schema from fields
     */
    buildPreviewSchema: function(fields) {
        const schema = {};
        
        for (const field of fields) {
            if (!field.name) continue;
            
            switch (field.type) {
                case 'text':
                    schema[field.name] = "text";
                    break;
                case 'number':
                    schema[field.name] = 0;
                    break;
                case 'boolean':
                    schema[field.name] = false;
                    break;
                case 'list':
                    schema[field.name] = [];
                    break;
                case 'group':
                    schema[field.name] = field.children ? this.buildPreviewSchema(field.children) : {};
                    break;
                case 'repeated_group':
                    schema[field.name] = field.children ? [this.buildPreviewSchema(field.children)] : [{}];
                    break;
                default:
                    schema[field.name] = null;
            }
        }
        
        return schema;
    },
    
    /**
     * Get current configuration
     */
    getConfig_legacy: function() {
        // Clean fields for storage (remove internal IDs)
        const cleanFields = this.cleanFieldsForStorage(this.currentFields);
        
        return {
            extractionType: document.getElementById('ai-extract-type')?.value || 'field_extraction',
            inputVariable: document.getElementById('ai-extract-input')?.value || '',
            outputVariable: document.getElementById('ai-extract-output')?.value || 'extractedData',
            specialInstructions: document.getElementById('ai-extract-instructions')?.value || '',
            failOnMissingRequired: document.getElementById('ai-extract-fail-required')?.checked || false,
            fields: cleanFields
        };
    },
    
    /**
     * Clean fields for storage (remove internal IDs)
     */
    cleanFieldsForStorage: function(fields) {
        return fields.map(field => {
            const cleaned = {
                name: field.name,
                type: field.type,
                required: field.required,
                description: field.description
            };
            
            if (field.children && field.children.length > 0) {
                cleaned.children = this.cleanFieldsForStorage(field.children);
            }
            
            return cleaned;
        }).filter(f => f.name); // Only include fields with names
    },

    openExcelSlider: function() {
        const slider = document.getElementById('ai-extract-excel-slider');
        const toggleText = document.getElementById('ai-extract-excel-toggle-text');
        const toggleIcon = document.getElementById('ai-extract-excel-toggle-icon');
        const modal = document.querySelector('#nodeConfigModal .modal-dialog');
        
        // Position slider to the right of the modal
        if (modal) {
            const modalRect = modal.getBoundingClientRect();
            slider.style.left = (modalRect.right + 10) + 'px';
        }
        
        slider.classList.add('open');
        toggleText.textContent = 'Hide Excel Options';
        toggleIcon.classList.remove('bi-chevron-right');
        toggleIcon.classList.add('bi-chevron-left');
    },

    // Toggle the horizontal Excel options slider
    toggleExcelSlider: function() {
        const slider = document.getElementById('ai-extract-excel-slider');
        
        if (slider.classList.contains('open')) {
            // Close
            slider.classList.remove('open');
            document.getElementById('ai-extract-excel-toggle-text').textContent = 'Show Excel Options';
            const toggleIcon = document.getElementById('ai-extract-excel-toggle-icon');
            toggleIcon.classList.remove('bi-chevron-left');
            toggleIcon.classList.add('bi-chevron-right');
        } else {
            // Open
            this.openExcelSlider();
        }
    },

    // Updated onOutputDestinationChange
    onOutputDestinationChange: function(value) {
        const excelToggle = document.getElementById('ai-extract-excel-toggle');
        const excelSlider = document.getElementById('ai-extract-excel-slider');
        const templateSection = document.getElementById('ai-extract-template-section');
        const mappingSection = document.getElementById('ai-extract-mapping-section');
        
        if (value === 'variable') {
            excelToggle.style.display = 'none';
            excelSlider.classList.remove('open');
        } else {
            excelToggle.style.display = 'block';
            
            if (value === 'excel_template') {
                // From Template: needs template path + mapping
                templateSection.style.display = 'block';
                mappingSection.style.display = 'block';
            } else if (value === 'excel_append') {
                // Append: needs mapping only (file already exists)
                templateSection.style.display = 'none';
                mappingSection.style.display = 'block';
            } else {
                // New file: no template, no mapping needed
                templateSection.style.display = 'none';
                mappingSection.style.display = 'none';
            }
        }
    },

    // Toggle AI vs manual mapping UI
    onMappingModeChange: function(value) {
        const aiMappingSection = document.getElementById('ai-extract-ai-mapping-section');
        const manualMappingSection = document.getElementById('ai-extract-manual-mapping-section');
        
        if (value === 'manual') {
            aiMappingSection.style.display = 'none';
            manualMappingSection.style.display = 'block';
            this.refreshMappingFields();
        } else {
            aiMappingSection.style.display = 'block';
            manualMappingSection.style.display = 'none';
        }
    },

    // Build manual mapping inputs from current fields
    refreshMappingFields: function() {
        const container = document.getElementById('ai-extract-mapping-container');
        const fields = this.currentFields || [];
        
        if (!container) {
            console.error('Mapping container not found');
            return;
        }
        
        if (!fields.length) {
            container.innerHTML = '<p class="text-muted small">Add extraction fields first</p>';
            return;
        }
        
        // PRESERVE EXISTING MAPPINGS before rebuilding
        // First try to get from the hidden field (most reliable source)
        let existingMappings = {};
        const hiddenField = document.getElementById('ai-extract-field-mapping');
        if (hiddenField && hiddenField.value) {
            try {
                existingMappings = JSON.parse(hiddenField.value);
            } catch (e) {
                console.warn('Could not parse existing field mapping:', e);
            }
        }
        
        // Also capture any values currently in input fields (in case hidden field is stale)
        document.querySelectorAll('.ai-extract-mapping-input').forEach(input => {
            const fieldName = input.dataset.field;
            const columnName = input.value.trim();
            if (fieldName && columnName) {
                existingMappings[fieldName] = columnName;
            }
        });
        
        console.log('Preserving existing mappings:', existingMappings);
        
        // Build new HTML
        let html = '';
        for (let i = 0; i < fields.length; i++) {
            const field = fields[i];
            const fieldName = field.name || '';
            if (fieldName) {
                // Check if we have an existing mapping for this field
                const existingValue = existingMappings[fieldName] || '';
                const escapedValue = existingValue.replace(/"/g, '&quot;');
                
                html += '<div class="d-flex align-items-center mb-2">';
                html += '<code class="bg-secondary text-white px-2 py-1 rounded me-2" style="min-width:200px;">' + fieldName + '</code>';
                html += '<span class="me-2">→</span>';
                html += '<input type="text" class="form-control form-control-sm ai-extract-mapping-input flex-grow-1" ';
                html += 'data-field="' + fieldName + '" placeholder="Excel column name" style="min-width:200px;" ';
                html += 'value="' + escapedValue + '" ';
                html += 'onchange="AIExtractNode.updateMappingHiddenField()" oninput="AIExtractNode.updateMappingHiddenField()">';
                html += '</div>';
            }
        }
        
        container.innerHTML = html || '<p class="text-muted small">No valid fields</p>';
        
        // Update the hidden field to ensure it's in sync
        this.updateMappingHiddenField();
    },

    updateMappingHiddenField: function() {
        const hiddenField = document.getElementById('ai-extract-field-mapping');
        if (!hiddenField) return;
        
        const mappings = {};
        document.querySelectorAll('.ai-extract-mapping-input').forEach(input => {
            const fieldName = input.dataset.field;
            const columnName = input.value.trim();
            if (fieldName && columnName) {
                mappings[fieldName] = columnName;
            }
        });
        
        hiddenField.value = JSON.stringify(mappings);
    },

/**
 * Get the current configuration from the UI
 */
getConfig: function() {
    console.log("Getting config...");
    const cleanFields = this.cleanFieldsForStorage(this.currentFields);
    
    const config = {
        extractionType: document.getElementById('ai-extract-type')?.value || 'field_extraction',
        inputSource: document.getElementById('ai-extract-input-source')?.value || 'auto',
        inputVariable: document.getElementById('ai-extract-input')?.value || '',
        outputVariable: document.getElementById('ai-extract-output')?.value || 'extractedData',
        specialInstructions: document.getElementById('ai-extract-instructions')?.value || '',
        failOnMissingRequired: document.getElementById('ai-extract-fail-required')?.checked || false,
        fields: cleanFields,
        // Include options - always saved at main level
        includeConfidence: document.getElementById('ai-extract-include-confidence')?.checked || false,
        includeAssumptions: document.getElementById('ai-extract-include-assumptions')?.checked || false,
        includeSources: document.getElementById('ai-extract-include-sources')?.checked || false
    };
    
    // Excel output configuration
    const outputDestination = document.getElementById('ai-extract-output-destination')?.value || 'variable';
    config.outputDestination = outputDestination;
    console.log("outputDestination:", outputDestination);

    if (outputDestination !== 'variable') {
        console.log("Setting up excel output...");
        config.outputToExcel = true;
        config.excelOutputPath = document.getElementById('ai-extract-excel-output-path')?.value || '';
        
        const operationMap = {
            'excel_new': 'new',
            'excel_template': 'new_from_template',
            'excel_append': 'append'
        };
        config.excelOperation = operationMap[outputDestination] || 'new';
        
        if (outputDestination === 'excel_template' || outputDestination === 'excel_append') {
            config.excelTemplatePath = document.getElementById('ai-extract-excel-template-path')?.value || '';
            
            const mappingMode = document.getElementById('ai-extract-mapping-mode')?.value || 'ai';
            
            if (mappingMode === 'manual') {
                const mappings = {};
                document.querySelectorAll('.ai-extract-mapping-input').forEach(input => {
                    const fieldName = input.dataset.field;
                    const columnName = input.value.trim();
                    if (fieldName && columnName) {
                        mappings[fieldName] = columnName;
                    }
                });
                if (Object.keys(mappings).length > 0) {
                    config.fieldMapping = mappings;
                }
            } else {
                config.aiMappingInstructions = document.getElementById('ai-extract-ai-mapping-instructions')?.value || '';
            }
        }
    } else {
        config.outputToExcel = false;
    }
    
    return config;
},

/**
 * Load configuration into the UI
 */
loadConfig: function(config) {
    if (!config) return;
    
    // Set basic fields
    const typeSelect = document.getElementById('ai-extract-type');
    if (typeSelect) typeSelect.value = config.extractionType || 'field_extraction';
    
    const inputSourceSelect = document.getElementById('ai-extract-input-source');
    if (inputSourceSelect) inputSourceSelect.value = config.inputSource || 'auto';
    
    const inputVar = document.getElementById('ai-extract-input');
    if (inputVar) inputVar.value = config.inputVariable || '';
    
    const outputVar = document.getElementById('ai-extract-output');
    if (outputVar) outputVar.value = config.outputVariable || 'extractedData';
    
    const instructions = document.getElementById('ai-extract-instructions');
    if (instructions) instructions.value = config.specialInstructions || '';
    
    const failCheck = document.getElementById('ai-extract-fail-required');
    if (failCheck) failCheck.checked = config.failOnMissingRequired || false;
    
    // Load include options (main level - always load)
    const includeConfidence = document.getElementById('ai-extract-include-confidence');
    if (includeConfidence) includeConfidence.checked = config.includeConfidence || false;
    
    const includeAssumptions = document.getElementById('ai-extract-include-assumptions');
    if (includeAssumptions) includeAssumptions.checked = config.includeAssumptions || false;
    
    const includeSources = document.getElementById('ai-extract-include-sources');
    if (includeSources) includeSources.checked = config.includeSources || false;
    
    // Load fields
    this.currentFields = this.addIdsToFields(config.fields || []);
    this.updateFieldsDisplay();
    this.updateOutputPreview();
    
    // Output destination - read directly from saved config
    const outputDestination = config.outputDestination || 'variable';
    
    const outputDestSelect = document.getElementById('ai-extract-output-destination');
    if (outputDestSelect) {
        outputDestSelect.value = outputDestination;
        this.onOutputDestinationChange(outputDestination);
    }
    
    // Excel options (only if Excel output selected)
    if (outputDestination !== 'variable') {
        const excelOutputPath = document.getElementById('ai-extract-excel-output-path');
        if (excelOutputPath) excelOutputPath.value = config.excelOutputPath || '';
        
        const excelTemplatePath = document.getElementById('ai-extract-excel-template-path');
        if (excelTemplatePath) excelTemplatePath.value = config.excelTemplatePath || '';
        
        // Mapping mode - read from config, then toggle visibility
        const mappingMode = config.mappingMode || 'ai';
        const mappingModeSelect = document.getElementById('ai-extract-mapping-mode');
        if (mappingModeSelect) {
            mappingModeSelect.value = mappingMode;
            this.onMappingModeChange(mappingMode);
        }
        
        // AI mapping instructions
        const aiMappingInstructions = document.getElementById('ai-extract-ai-mapping-instructions');
        if (aiMappingInstructions) aiMappingInstructions.value = config.aiMappingInstructions || '';
        
        // Load manual field mappings if present
        if (config.fieldMapping) {
            let mappings = config.fieldMapping;
            if (typeof mappings === 'string' && mappings) {
                try {
                    mappings = JSON.parse(mappings);
                } catch (e) {
                    mappings = {};
                }
            }
            
            if (mappings && Object.keys(mappings).length > 0) {
                setTimeout(() => {
                    document.querySelectorAll('.ai-extract-mapping-input').forEach(input => {
                        const fieldName = input.dataset.field;
                        if (fieldName && mappings[fieldName]) {
                            input.value = mappings[fieldName];
                        }
                    });
                    this.updateMappingHiddenField();
                }, 150);
            }
        }
    }

    if (outputDestination !== 'variable') {
        // Auto-open the slider if there are Excel settings configured
        const excelPath = document.getElementById('ai-extract-excel-output-path')?.value;
        if (excelPath) {
            document.getElementById('ai-extract-excel-slider').classList.add('open');
            document.getElementById('ai-extract-excel-toggle-text').textContent = 'Hide Excel Options';
            const toggleIcon = document.getElementById('ai-extract-excel-toggle-icon');
            toggleIcon.classList.remove('bi-chevron-right');
            toggleIcon.classList.add('bi-chevron-left');
        }
    }

    if (outputDestination !== 'variable') {
        // Auto-open the slider if there are Excel settings configured
        const excelPath = document.getElementById('ai-extract-excel-output-path')?.value;
        if (excelPath) {
            // Use setTimeout to ensure modal is fully rendered
            setTimeout(() => {
                this.openExcelSlider();
            }, 100);
        }
    }
},

/**
 * Load manual field mappings into the UI
 */
loadManualMappings: function(mappings) {
    if (!mappings) return;
    
    document.querySelectorAll('.ai-extract-mapping-input').forEach(input => {
        const fieldName = input.dataset.field;
        if (fieldName && mappings[fieldName]) {
            input.value = mappings[fieldName];
        }
    });
},
    
    
    /**
     * Load configuration into the UI
     */
    loadConfig_legacy: function(config) {
        if (!config) return;
        
        // Set basic fields
        const typeSelect = document.getElementById('ai-extract-type');
        if (typeSelect) typeSelect.value = config.extractionType || 'field_extraction';
        
        const inputVar = document.getElementById('ai-extract-input');
        if (inputVar) inputVar.value = config.inputVariable || '';
        
        const outputVar = document.getElementById('ai-extract-output');
        if (outputVar) outputVar.value = config.outputVariable || 'extractedData';
        
        const instructions = document.getElementById('ai-extract-instructions');
        if (instructions) instructions.value = config.specialInstructions || '';
        
        const failCheck = document.getElementById('ai-extract-fail-required');
        if (failCheck) failCheck.checked = config.failOnMissingRequired || false;
        
        // Load fields
        this.currentFields = this.addIdsToFields(config.fields || []);
        this.updateFieldsDisplay();
        this.updateOutputPreview();
    },
    
    /**
     * Add internal IDs to fields for editing
     */
    addIdsToFields: function(fields) {
        return fields.map(field => {
            const withId = {
                ...field,
                id: `field_${this.fieldIdCounter++}`
            };
            
            if (field.children && field.children.length > 0) {
                withId.children = this.addIdsToFields(field.children);
            }
            
            return withId;
        });
    },
    
    /**
     * Test extraction with sample content
     */
    testExtraction: function() {
        // Get current config
        const config = this.getConfig();
        
        if (config.fields.length === 0) {
            alert('Please add at least one field before testing.');
            return;
        }
        
        // Show test modal
        this.showTestModal(config);
    },
    
    /**
     * Show the test extraction modal
     */
    showTestModal: function(config) {
        // Check if modal exists, create if not
        let modal = document.getElementById('ai-extract-test-modal');
        
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'ai-extract-test-modal';
            modal.className = 'modal fade';
            modal.innerHTML = `
                <div class="modal-dialog modal-lg">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">Test Extraction</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <div class="mb-3">
                                <label class="form-label fw-bold">Sample Content</label>
                                <textarea id="ai-extract-test-content" class="form-control" rows="8" 
                                          placeholder="Paste sample content here to test extraction..."></textarea>
                            </div>
                            
                            <div id="ai-extract-test-result" style="display: none;">
                                <label class="form-label fw-bold">Extraction Result</label>
                                <div id="ai-extract-test-status" class="alert mb-2"></div>
                                <pre id="ai-extract-test-output" class="bg-light border rounded p-2" 
                                     style="max-height: 300px; overflow-y: auto;"></pre>
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
                            <button type="button" class="btn btn-primary" id="ai-extract-run-test" 
                                    onclick="AIExtractNode.runTest()">
                                <i class="bi bi-play"></i> Run Test
                            </button>
                        </div>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
        }
        
        // Store config for test
        this._testConfig = config;
        
        // Reset modal state
        document.getElementById('ai-extract-test-content').value = '';
        document.getElementById('ai-extract-test-result').style.display = 'none';
        
        // Show modal
        const bsModal = new bootstrap.Modal(modal);
        bsModal.show();
    },
    
    /**
     * Run the extraction test
     */
    runTest: async function() {
        const testContent = document.getElementById('ai-extract-test-content').value;
        
        if (!testContent.trim()) {
            alert('Please enter sample content to test.');
            return;
        }
        
        const config = this._testConfig;
        const runBtn = document.getElementById('ai-extract-run-test');
        const resultDiv = document.getElementById('ai-extract-test-result');
        const statusDiv = document.getElementById('ai-extract-test-status');
        const outputPre = document.getElementById('ai-extract-test-output');
        
        // Show loading state
        runBtn.disabled = true;
        runBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Testing...';
        resultDiv.style.display = 'block';
        statusDiv.className = 'alert alert-info mb-2';
        statusDiv.textContent = 'Running extraction...';
        outputPre.textContent = '';
        
        try {
            const response = await fetch('/api/workflow/ai-extract/test', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    extraction_type: config.extractionType,
                    fields: config.fields,
                    special_instructions: config.specialInstructions,
                    test_content: testContent,
                    fail_on_missing_required: config.failOnMissingRequired
                })
            });
            
            const result = await response.json();
            
            if (result.success) {
                statusDiv.className = 'alert alert-success mb-2';
                let statusText = '✓ Extraction successful';
                
                if (result.validation) {
                    if (result.validation.all_required_found) {
                        statusText += ' - All required fields found';
                    } else {
                        statusText += ` - Missing required: ${result.validation.missing_required.join(', ')}`;
                        statusDiv.className = 'alert alert-warning mb-2';
                    }
                }
                
                statusDiv.textContent = statusText;
                outputPre.textContent = JSON.stringify(result.result, null, 2);
            } else {
                statusDiv.className = 'alert alert-danger mb-2';
                statusDiv.textContent = `✗ Extraction failed: ${result.error}`;
                
                if (result.result) {
                    outputPre.textContent = JSON.stringify(result.result, null, 2);
                } else if (result.raw_response) {
                    outputPre.textContent = `Raw AI response:\n${result.raw_response}`;
                }
            }
            
        } catch (error) {
            statusDiv.className = 'alert alert-danger mb-2';
            statusDiv.textContent = `✗ Error: ${error.message}`;
        } finally {
            runBtn.disabled = false;
            runBtn.innerHTML = '<i class="bi bi-play"></i> Run Test';
        }
    },
    
    /**
     * Escape HTML entities
     */
    escapeHtml: function(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
};


// CSS Styles for AI Extract Node
const AIExtractNodeStyles = `
<style>
/* AI Extract Node Styles */
.ai-extract-config .fields-container {
    max-height: 200px;
    overflow-y: auto;
}

.ai-extract-config .field-item {
    border-left: 3px solid #0d6efd;
}

.ai-extract-config .field-item .card-body {
    background: #f8f9fa;
}

.ai-extract-config .children-container {
    border-color: #6c757d !important;
}

.ai-extract-config .children-container .field-item {
    border-left-color: #6c757d;
}

.ai-extract-config .field-name-input.is-invalid {
    border-color: #dc3545;
    background-color: #fff5f5;
}

.ai-extract-config .field-name-input.is-valid {
    border-color: #198754;
}

.ai-extract-config #ai-extract-preview {
    font-family: 'Courier New', monospace;
    background-color: #f8f9fa;
}

/* Tool item styling for palette */
.tool-item.ai-extract {
    background: linear-gradient(135deg, #6f42c1 0%, #d63384 100%);
    color: white;
    border: none;
}

.tool-item.ai-extract:hover {
    background: linear-gradient(135deg, #5a32a3 0%, #b02a6d 100%);
    transform: translateX(2px);
}

/* Node styling on canvas */
.workflow-node[data-type="AI Extract"] {
    background: linear-gradient(135deg, #6f42c1 0%, #d63384 100%);
}

.workflow-node[data-type="AI Extract"] .node-content {
    color: white;
}

.workflow-node[data-type="AI Extract"]:hover {
    box-shadow: 0 4px 12px rgba(111, 66, 193, 0.4);
}
</style>
`;


// Export for use in workflow.js
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { AIExtractNodeTemplate, AIExtractNode, AIExtractNodeStyles };
}
