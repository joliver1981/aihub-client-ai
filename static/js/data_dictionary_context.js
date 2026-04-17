/**
 * Page Context: Data Dictionary
 * 
 * Provides real-time context about the data dictionary page state.
 * Add this script to data_dictionary.html
 * 
 * Extracts:
 * - Selected connection and statistics
 * - Current table/column being edited with all metadata
 * - Active tab and form states
 * - AI Discovery progress
 * - Validation status
 */

window.assistantPageContext = {
    page: 'data_dictionary',
    pageName: 'Data Dictionary',
    
    getPageData: function() {
        const data = {
            // Connection context
            connection: {
                id: null,
                name: 'None selected',
                isSelected: false
            },
            
            // Statistics
            statistics: {
                tableCount: 0,
                columnCount: 0,
                enhancedPercent: 0,
                tablesEnhanced: 0,
                columnsEnhanced: 0
            },
            
            // Active tab
            activeTab: 'Tables',
            
            // Table context
            tables: {
                total: 0,
                enhanced: 0,
                list: [],
                searchFilter: '',
                currentTable: null
            },
            
            // Column context
            columns: {
                total: 0,
                enhanced: 0,
                selectedTableId: null,
                selectedTableName: '',
                searchFilter: '',
                currentColumn: null
            },
            
            // AI Discovery state
            discovery: {
                isActive: false,
                discoveredTables: 0,
                selectedTables: 0,
                isAnalyzing: false,
                progress: 0
            },
            
            // Form states
            formState: {
                tableFormVisible: false,
                columnFormVisible: false,
                hasUnsavedChanges: false,
                isCreatingNew: false
            },
            
            // Available actions
            availableActions: [],
            
            // Validation
            validation: {
                isValid: true,
                errors: []
            }
        };
        
        // === CONNECTION ===
        const connectionSelect = document.getElementById('connectionSelect');
        if (connectionSelect && connectionSelect.value) {
            data.connection.id = connectionSelect.value;
            data.connection.name = connectionSelect.options[connectionSelect.selectedIndex]?.text || '';
            data.connection.isSelected = true;
        }
        
        // === STATISTICS FROM BADGES ===
        const tableCountBadge = document.getElementById('tableCount');
        const columnCountBadge = document.getElementById('columnCount');
        const enhancedCountBadge = document.getElementById('enhancedCount');
        
        if (tableCountBadge) {
            const match = tableCountBadge.textContent.match(/(\d+)/);
            data.statistics.tableCount = match ? parseInt(match[1]) : 0;
        }
        if (columnCountBadge) {
            const match = columnCountBadge.textContent.match(/(\d+)/);
            data.statistics.columnCount = match ? parseInt(match[1]) : 0;
        }
        if (enhancedCountBadge) {
            const match = enhancedCountBadge.textContent.match(/(\d+)/);
            data.statistics.enhancedPercent = match ? parseInt(match[1]) : 0;
        }
        
        // === ACTIVE TAB ===
        const activeTabLink = document.querySelector('.nav-tabs .nav-link.active');
        if (activeTabLink) {
            data.activeTab = activeTabLink.textContent.trim();
        }
        
        // === TABLES ===
        // Get from global variable if available
        if (typeof allTables !== 'undefined' && Array.isArray(allTables)) {
            data.tables.total = allTables.length;
            data.tables.enhanced = allTables.filter(t => t.table_description || t.business_name).length;
            
            // Include summary of tables (first 10)
            data.tables.list = allTables.slice(0, 10).map(t => ({
                name: t.table_name,
                schema: t.table_schema,
                type: t.table_type,
                hasDescription: !!t.table_description,
                businessName: t.business_name || ''
            }));
        }
        
        // Table search filter
        const tableSearchInput = document.getElementById('tableSearchInput');
        if (tableSearchInput) {
            data.tables.searchFilter = tableSearchInput.value.trim();
        }
        
        // Current table being edited
        if (typeof currentTable !== 'undefined' && currentTable) {
            data.tables.currentTable = {
                id: currentTable.id,
                name: currentTable.table_name,
                schema: currentTable.table_schema,
                type: currentTable.table_type || 'Not set',
                businessName: currentTable.business_name || '',
                description: currentTable.table_description ? 
                    (currentTable.table_description.length > 100 ? 
                        currentTable.table_description.substring(0, 100) + '...' : 
                        currentTable.table_description) : '',
                hasDescription: !!currentTable.table_description,
                hasPrimaryKeys: !!currentTable.primary_keys,
                hasForeignKeys: !!currentTable.foreign_keys,
                hasBusinessRules: !!currentTable.business_rules,
                hasRelatedTables: !!currentTable.related_tables,
                hasCommonFilters: !!currentTable.common_filters,
                hasSynonyms: !!currentTable.synonyms,
                isNew: !currentTable.id
            };
        }
        
        // === COLUMNS ===
        // Column table selector
        const columnTableSelect = document.getElementById('columnTableSelect');
        if (columnTableSelect && columnTableSelect.value) {
            data.columns.selectedTableId = columnTableSelect.value;
            data.columns.selectedTableName = columnTableSelect.options[columnTableSelect.selectedIndex]?.text || '';
        }
        
        // Get from global variable if available
        if (typeof allColumns !== 'undefined' && Array.isArray(allColumns)) {
            data.columns.total = allColumns.length;
            data.columns.enhanced = allColumns.filter(c => c.column_description || c.business_name).length;
        }
        
        // Column search filter
        const columnSearchInput = document.getElementById('columnSearchInput');
        if (columnSearchInput) {
            data.columns.searchFilter = columnSearchInput.value.trim();
        }
        
        // Current column being edited
        if (typeof currentColumn !== 'undefined' && currentColumn) {
            data.columns.currentColumn = {
                id: currentColumn.id,
                name: currentColumn.column_name,
                dataType: currentColumn.data_type,
                businessName: currentColumn.business_name || '',
                description: currentColumn.column_description ? 
                    (currentColumn.column_description.length > 100 ? 
                        currentColumn.column_description.substring(0, 100) + '...' : 
                        currentColumn.column_description) : '',
                hasDescription: !!currentColumn.column_description,
                isPrimaryKey: !!currentColumn.is_primary_key,
                isForeignKey: !!currentColumn.is_foreign_key,
                foreignKeyTable: currentColumn.foreign_key_table || null,
                isCalculated: !!currentColumn.is_calculated,
                calculationFormula: currentColumn.calculation_formula || null,
                isSensitive: !!currentColumn.is_sensitive,
                isNullable: currentColumn.is_nullable !== false,
                hasExamples: !!currentColumn.examples,
                hasSynonyms: !!currentColumn.synonyms,
                units: currentColumn.units || null,
                isNew: !currentColumn.id
            };
        }
        
        // === AI DISCOVERY TAB ===
        if (data.activeTab.includes('Discovery')) {
            data.discovery.isActive = true;
            
            // Count discovered tables
            const tableCheckboxes = document.querySelectorAll('input[name="discoverTables"]');
            data.discovery.discoveredTables = tableCheckboxes.length;
            data.discovery.selectedTables = document.querySelectorAll('input[name="discoverTables"]:checked').length;
            
            // Check if analysis is in progress
            const progressBar = document.getElementById('analysisProgressBar');
            const analysisProgress = document.getElementById('analysisProgress');
            if (analysisProgress && analysisProgress.style.display !== 'none') {
                data.discovery.isAnalyzing = true;
                if (progressBar) {
                    const widthMatch = progressBar.style.width.match(/(\d+)/);
                    data.discovery.progress = widthMatch ? parseInt(widthMatch[1]) : 0;
                }
            }
        }
        
        // === FORM STATES ===
        const tableFormContainer = document.getElementById('tableFormContainer');
        const columnFormContainer = document.getElementById('columnFormContainer');
        
        // Check if forms have content (not just placeholder)
        if (tableFormContainer) {
            const hasForm = tableFormContainer.querySelector('input, textarea, select');
            data.formState.tableFormVisible = !!hasForm;
        }
        if (columnFormContainer) {
            const hasForm = columnFormContainer.querySelector('input, textarea, select');
            data.formState.columnFormVisible = !!hasForm;
        }
        
        // Check for new item creation
        const tableFormTitle = document.getElementById('tableFormTitle');
        const columnFormTitle = document.getElementById('columnFormTitle');
        if (tableFormTitle && tableFormTitle.textContent.includes('New')) {
            data.formState.isCreatingNew = true;
        }
        if (columnFormTitle && columnFormTitle.textContent.includes('New')) {
            data.formState.isCreatingNew = true;
        }
        
        // === AVAILABLE ACTIONS ===
        if (!data.connection.isSelected) {
            data.availableActions = [
                'Select a database connection to begin',
                'View help documentation'
            ];
        } else if (data.activeTab.includes('Discovery')) {
            if (data.discovery.discoveredTables === 0) {
                data.availableActions = [
                    'Click "Discover Tables from Database" to scan for tables',
                    'Switch to Tables tab to manually add tables'
                ];
            } else if (data.discovery.selectedTables === 0) {
                data.availableActions = [
                    'Select tables to analyze with AI',
                    'Use "Select All" for bulk selection'
                ];
            } else {
                data.availableActions = [
                    'Click "AI Auto-Populate" to generate metadata',
                    'Deselect tables you want to skip'
                ];
            }
        } else if (data.activeTab === 'Tables' || data.activeTab.includes('Tables')) {
            if (data.tables.currentTable) {
                data.availableActions = [
                    'Edit table metadata fields',
                    'Add business name and description',
                    'Configure business rules',
                    'Define related tables',
                    'Save changes',
                    'Delete table'
                ];
            } else {
                data.availableActions = [
                    'Select a table from the list',
                    'Add a new table',
                    'Refresh tables from database',
                    'Search for specific tables'
                ];
            }
        } else if (data.activeTab === 'Columns' || data.activeTab.includes('Columns')) {
            if (data.columns.currentColumn) {
                data.availableActions = [
                    'Edit column metadata',
                    'Add business name and description',
                    'Mark as primary/foreign key',
                    'Add calculation formula (if calculated)',
                    'Mark as sensitive data',
                    'Save changes'
                ];
            } else if (data.columns.selectedTableId) {
                data.availableActions = [
                    'Select a column from the list',
                    'Add a new column',
                    'Search for specific columns'
                ];
            } else {
                data.availableActions = [
                    'Select a table first',
                    'Then select a column to edit'
                ];
            }
        } else if (data.activeTab.includes('Bulk')) {
            data.availableActions = [
                'Export dictionary to CSV or Excel',
                'Import metadata from file',
                'Download import template',
                'Run validation check'
            ];
        }
        
        // === VALIDATION ===
        if (data.formState.tableFormVisible && data.tables.currentTable) {
            if (!data.tables.currentTable.businessName && !data.tables.currentTable.hasDescription) {
                data.validation.errors.push('Table needs business name or description');
            }
        }
        if (data.formState.columnFormVisible && data.columns.currentColumn) {
            if (!data.columns.currentColumn.businessName && !data.columns.currentColumn.hasDescription) {
                data.validation.errors.push('Column needs business name or description');
            }
            if (data.columns.currentColumn.isCalculated && !data.columns.currentColumn.calculationFormula) {
                data.validation.errors.push('Calculated column needs a formula');
            }
            if (data.columns.currentColumn.isForeignKey && !data.columns.currentColumn.foreignKeyTable) {
                data.validation.errors.push('Foreign key needs reference table');
            }
        }
        data.validation.isValid = data.validation.errors.length === 0;
        
        // Debug summary
        console.log('=== Data Dictionary Context ===');
        console.log('Connection:', data.connection.name);
        console.log('Active tab:', data.activeTab);
        console.log('Tables:', data.tables.total, '| Enhanced:', data.tables.enhanced);
        console.log('Columns:', data.columns.total, '| Enhanced:', data.columns.enhanced);
        if (data.tables.currentTable) {
            console.log('Current table:', data.tables.currentTable.name, 
                       '| Type:', data.tables.currentTable.type,
                       '| Has desc:', data.tables.currentTable.hasDescription);
        }
        if (data.columns.currentColumn) {
            console.log('Current column:', data.columns.currentColumn.name,
                       '| Type:', data.columns.currentColumn.dataType,
                       '| PK:', data.columns.currentColumn.isPrimaryKey,
                       '| FK:', data.columns.currentColumn.isForeignKey);
        }
        if (data.discovery.isActive) {
            console.log('Discovery: Discovered:', data.discovery.discoveredTables,
                       '| Selected:', data.discovery.selectedTables,
                       '| Analyzing:', data.discovery.isAnalyzing);
        }
        
        return data;
    }
};

console.log('Data Dictionary context loaded');
