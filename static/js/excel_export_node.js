// excel_export_node.js
// Excel Export Node - Write variable data to Excel files with UPDATE support

/**
 * Excel Export Node Handler
 * Manages the Excel Export node configuration UI and logic
 * 
 * Supports operations: new, template, append, UPDATE
 * UPDATE includes AI-assisted key matching option
 */
const ExcelExportNode = {
    // Track current field list for manual mapping
    currentFields: [],

    /**
     * Initialize the node when modal opens
     */
    init: function() {
        console.log('ExcelExportNode initialized');
        // Initialize visibility based on current operation
        const operation = document.getElementById('excel-export-operation');
        if (operation) {
            this.onOperationChange(operation.value);
        }
    },

    /**
     * Handle operation mode change (new, template, append, update)
     */
    onOperationChange: function(value) {
        const templateSection = document.getElementById('excel-export-template-section');
        const mappingSection = document.getElementById('excel-export-mapping-section');
        const updateSection = document.getElementById('excel-export-update-section');
        
        const isUpdateMode = value === 'update';
        
        // Template path: show for template and append, hide for new and update
        if (templateSection) {
            templateSection.style.display = (value === 'template' || value === 'append') ? 'block' : 'none';
        }
        
        // Mapping section: show for template, append, and update
        if (mappingSection) {
            mappingSection.style.display = (value === 'template' || value === 'append' || value === 'update') ? 'block' : 'none';
        }
        
        // Update section: show only for update mode
        if (updateSection) {
            updateSection.style.display = isUpdateMode ? 'block' : 'none';
        }
        
        // Update help text for Output Path
        const operationHelp = document.getElementById('excel-export-operation-help');
        if (operationHelp) {
            if (isUpdateMode) {
                operationHelp.textContent = 'UPDATE: Matches rows by key columns, updates values, highlights changes';
                operationHelp.style.display = 'block';
            } else if (value === 'append') {
                operationHelp.textContent = 'APPEND: Adds new rows to the end of the file';
                operationHelp.style.display = 'block';
            } else {
                operationHelp.style.display = 'none';
            }
        }
    },

    /**
     * Handle AI key matching checkbox toggle
     */
    onAIKeyMatchingChange: function(checked) {
        const instructionsSection = document.getElementById('excel-export-ai-key-instructions-section');
        if (instructionsSection) {
            instructionsSection.style.display = checked ? 'block' : 'none';
        }
    },

    /**
     * Handle Smart Change Detection checkbox toggle
     */
    onSmartChangeDetectionChange: function(checked) {
        const optionsSection = document.getElementById('excel-export-smart-change-options');
        if (optionsSection) {
            optionsSection.style.display = checked ? 'block' : 'none';
        }
    },

    /**
     * Handle mapping mode change (AI vs manual)
     */
    onMappingModeChange: function(value) {
        const aiMappingSection = document.getElementById('excel-export-ai-mapping-section');
        const manualMappingSection = document.getElementById('excel-export-manual-mapping-section');
        
        if (value === 'manual') {
            if (aiMappingSection) aiMappingSection.style.display = 'none';
            if (manualMappingSection) manualMappingSection.style.display = 'block';
            this.refreshMappingFields();
        } else {
            if (aiMappingSection) aiMappingSection.style.display = 'block';
            if (manualMappingSection) manualMappingSection.style.display = 'none';
        }
    },

    /**
     * Parse the input variable to detect available fields for mapping
     */
    detectFieldsFromInput: function() {
        const inputVar = document.getElementById('excel-export-input-variable')?.value || '';
        const carryForward = document.getElementById('excel-export-carry-forward')?.value || '';
        
        // Build field list from carry-forward fields
        let fields = [];
        
        // Parse carry-forward fields
        if (carryForward) {
            const cfFields = carryForward.split(',').map(f => f.trim()).filter(f => f);
            cfFields.forEach(f => {
                fields.push({ name: f, source: 'carry-forward' });
            });
        }
        
        // Try to get fields from manual input
        const manualFields = document.getElementById('excel-export-manual-fields')?.value || '';
        if (manualFields) {
            const mfList = manualFields.split(',').map(f => f.trim()).filter(f => f);
            mfList.forEach(f => {
                if (!fields.find(ef => ef.name === f)) {
                    fields.push({ name: f, source: 'manual' });
                }
            });
        }
        
        this.currentFields = fields;
        return fields;
    },

    /**
     * Build manual mapping inputs from detected fields
     */
    refreshMappingFields: function() {
        const container = document.getElementById('excel-export-mapping-container');
        
        if (!container) {
            console.error('Mapping container not found');
            return;
        }
        
        // Get fields from manual field list
        this.detectFieldsFromInput();
        const fields = this.currentFields;
        
        if (!fields.length) {
            container.innerHTML = '<p class="text-muted small">Enter field names above, then click Refresh</p>';
            return;
        }
        
        // Preserve existing mapping values
        let existingMappings = {};
        const hiddenField = document.getElementById('excel-export-field-mapping');
        if (hiddenField && hiddenField.value) {
            try {
                existingMappings = JSON.parse(hiddenField.value);
            } catch (e) { }
        }
        
        // Also get from current inputs
        document.querySelectorAll('.excel-export-mapping-input').forEach(input => {
            const fieldName = input.dataset.field;
            const columnName = input.value.trim();
            if (fieldName && columnName) {
                existingMappings[fieldName] = columnName;
            }
        });
        
        // Build HTML
        let html = '';
        for (const field of fields) {
            const fieldName = field.name;
            const existingValue = existingMappings[fieldName] || '';
            const escapedValue = existingValue.replace(/"/g, '&quot;');
            const sourceLabel = field.source === 'carry-forward' ? 
                '<span class="badge bg-info ms-1" title="Carry-forward field">CF</span>' : '';
            
            html += '<div class="d-flex align-items-center mb-2">';
            html += '<code class="bg-secondary text-white px-2 py-1 rounded me-2" style="min-width:150px;">' + 
                    fieldName + sourceLabel + '</code>';
            html += '<span class="me-2">→</span>';
            html += '<input type="text" class="form-control form-control-sm excel-export-mapping-input flex-grow-1" ';
            html += 'data-field="' + fieldName + '" ';
            html += 'placeholder="Excel column name" ';
            html += 'style="min-width:150px;" ';
            html += 'value="' + escapedValue + '" ';
            html += 'onchange="ExcelExportNode.updateMappingHiddenField()" ';
            html += 'oninput="ExcelExportNode.updateMappingHiddenField()">';
            html += '</div>';
        }
        
        container.innerHTML = html || '<p class="text-muted small">No fields detected</p>';
        
        // Update hidden field
        this.updateMappingHiddenField();
    },

    /**
     * Update the hidden field with current mappings
     */
    updateMappingHiddenField: function() {
        const hiddenField = document.getElementById('excel-export-field-mapping');
        if (!hiddenField) return;
        
        const mappings = {};
        document.querySelectorAll('.excel-export-mapping-input').forEach(input => {
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
        const config = {
            // Basic config
            inputVariable: document.getElementById('excel-export-input-variable')?.value || '',
            flattenArray: document.getElementById('excel-export-flatten')?.checked || false,
            carryForwardFields: document.getElementById('excel-export-carry-forward')?.value || '',
            excelOutputPath: document.getElementById('excel-export-output-path')?.value || '',
            excelOperation: document.getElementById('excel-export-operation')?.value || 'append',
            excelTemplatePath: document.getElementById('excel-export-template-path')?.value || '',
            excelSheetName: document.getElementById('excel-export-sheet-name')?.value || '',
            mappingMode: document.getElementById('excel-export-mapping-mode')?.value || 'ai',
            aiMappingInstructions: document.getElementById('excel-export-ai-mapping-instructions')?.value || '',
            fieldMapping: null,
            manualFields: document.getElementById('excel-export-manual-fields')?.value || '',
            
            // UPDATE operation config
            keyColumns: document.getElementById('excel-export-key-columns')?.value || '',
            highlightChanges: document.getElementById('excel-export-highlight-changes')?.checked ?? true,
            changeHighlightColor: document.getElementById('excel-export-change-color')?.value || '#FFFF00',
            newRowColor: document.getElementById('excel-export-new-row-color')?.value || '#90EE90',
            deletedRowColor: document.getElementById('excel-export-deleted-row-color')?.value || '#FFB6C1',
            trackDeletedRows: document.getElementById('excel-export-track-deleted')?.checked ?? false,
            addNewRecords: document.getElementById('excel-export-add-new-records')?.checked ?? true,
            markDeletedAs: document.getElementById('excel-export-mark-deleted-as')?.value || 'strikethrough',
            addChangeTimestamp: document.getElementById('excel-export-add-timestamp')?.checked ?? true,
            timestampColumn: document.getElementById('excel-export-timestamp-column')?.value || 'Last Updated',
            changeLogSheet: document.getElementById('excel-export-change-log-sheet')?.value || '',
            
            // AI Key Matching config
            useAIKeyMatching: document.getElementById('excel-export-use-ai-key-matching')?.checked ?? false,
            aiKeyMatchingInstructions: document.getElementById('excel-export-ai-key-instructions')?.value || '',
            
            // Smart Change Detection config
            useSmartChangeDetection: document.getElementById('excel-export-use-smart-change-detection')?.checked ?? false,
            smartChangeStrictness: document.getElementById('excel-export-smart-change-strictness')?.value || 'strict'
        };
        
        // Get field mapping if in manual mode
        if (config.mappingMode === 'manual') {
            const hiddenField = document.getElementById('excel-export-field-mapping');
            if (hiddenField && hiddenField.value) {
                try {
                    config.fieldMapping = JSON.parse(hiddenField.value);
                } catch (e) {
                    config.fieldMapping = null;
                }
            }
        }
        
        return config;
    },

    /**
     * Load configuration into the UI
     */
    loadConfig: function(config) {
        if (!config) return;
        
        // Basic config
        const inputVar = document.getElementById('excel-export-input-variable');
        if (inputVar) inputVar.value = config.inputVariable || '';
        
        const flatten = document.getElementById('excel-export-flatten');
        if (flatten) flatten.checked = config.flattenArray || false;
        
        const carryForward = document.getElementById('excel-export-carry-forward');
        if (carryForward) carryForward.value = config.carryForwardFields || '';
        
        const outputPath = document.getElementById('excel-export-output-path');
        if (outputPath) outputPath.value = config.excelOutputPath || '';
        
        const operation = document.getElementById('excel-export-operation');
        if (operation) {
            operation.value = config.excelOperation || 'append';
            this.onOperationChange(operation.value);
        }
        
        const templatePath = document.getElementById('excel-export-template-path');
        if (templatePath) templatePath.value = config.excelTemplatePath || '';
        
        const sheetName = document.getElementById('excel-export-sheet-name');
        if (sheetName) sheetName.value = config.excelSheetName || '';
        
        const manualFields = document.getElementById('excel-export-manual-fields');
        if (manualFields) manualFields.value = config.manualFields || '';
        
        const mappingMode = document.getElementById('excel-export-mapping-mode');
        if (mappingMode) {
            mappingMode.value = config.mappingMode || 'ai';
            this.onMappingModeChange(mappingMode.value);
        }
        
        const aiInstructions = document.getElementById('excel-export-ai-mapping-instructions');
        if (aiInstructions) aiInstructions.value = config.aiMappingInstructions || '';
        
        // UPDATE operation config
        const keyColumns = document.getElementById('excel-export-key-columns');
        if (keyColumns) keyColumns.value = config.keyColumns || '';
        
        const highlightChanges = document.getElementById('excel-export-highlight-changes');
        if (highlightChanges) highlightChanges.checked = config.highlightChanges ?? true;
        
        const changeColor = document.getElementById('excel-export-change-color');
        if (changeColor) changeColor.value = config.changeHighlightColor || '#FFFF00';
        
        const newRowColor = document.getElementById('excel-export-new-row-color');
        if (newRowColor) newRowColor.value = config.newRowColor || '#90EE90';
        
        const deletedRowColor = document.getElementById('excel-export-deleted-row-color');
        if (deletedRowColor) deletedRowColor.value = config.deletedRowColor || '#FFB6C1';
        
        const trackDeleted = document.getElementById('excel-export-track-deleted');
        if (trackDeleted) trackDeleted.checked = config.trackDeletedRows ?? false;

        const addNewRecords = document.getElementById('excel-export-add-new-records');
        if (addNewRecords) addNewRecords.checked = config.addNewRecords ?? true;
        
        const markDeletedAs = document.getElementById('excel-export-mark-deleted-as');
        if (markDeletedAs) markDeletedAs.value = config.markDeletedAs || 'strikethrough';
        
        const addTimestamp = document.getElementById('excel-export-add-timestamp');
        if (addTimestamp) addTimestamp.checked = config.addChangeTimestamp ?? true;
        
        const timestampCol = document.getElementById('excel-export-timestamp-column');
        if (timestampCol) timestampCol.value = config.timestampColumn || 'Last Updated';
        
        const changeLogSheet = document.getElementById('excel-export-change-log-sheet');
        if (changeLogSheet) changeLogSheet.value = config.changeLogSheet || '';
        
        // AI Key Matching config
        const useAIKeyMatching = document.getElementById('excel-export-use-ai-key-matching');
        if (useAIKeyMatching) {
            useAIKeyMatching.checked = config.useAIKeyMatching ?? false;
            this.onAIKeyMatchingChange(useAIKeyMatching.checked);
        }
        
        const aiKeyInstructions = document.getElementById('excel-export-ai-key-instructions');
        if (aiKeyInstructions) aiKeyInstructions.value = config.aiKeyMatchingInstructions || '';
        
        // Smart Change Detection config
        const useSmartChangeDetection = document.getElementById('excel-export-use-smart-change-detection');
        if (useSmartChangeDetection) {
            useSmartChangeDetection.checked = config.useSmartChangeDetection ?? false;
            this.onSmartChangeDetectionChange(useSmartChangeDetection.checked);
        }
        
        const smartChangeStrictness = document.getElementById('excel-export-smart-change-strictness');
        if (smartChangeStrictness) smartChangeStrictness.value = config.smartChangeStrictness || 'strict';
        
        // Load field mapping
        if (config.fieldMapping) {
            const hiddenField = document.getElementById('excel-export-field-mapping');
            if (hiddenField) {
                hiddenField.value = JSON.stringify(config.fieldMapping);
            }
            
            // Refresh mapping display after a short delay
            setTimeout(() => {
                this.refreshMappingFields();
            }, 100);
        }
    }
};

// Export for use in workflow.js (if using modules)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { ExcelExportNode };
}
