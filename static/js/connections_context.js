/**
 * Page Context: Connections
 * 
 * Add this script block to connections.html before the closing </script> tag
 * or in a separate file included after the main page script.
 * 
 * This provides the AI assistant with comprehensive real-time context about:
 * - Available connection types and categories
 * - Current connections list
 * - Selected/editing connection details
 * - Form state and validation
 * - ODBC driver availability
 */

window.assistantPageContext = {
    page: 'connections',
    pageName: 'Database Connections',
    
    getPageData: function() {
        const data = {
            // Connection list
            connections: {
                total: typeof allConnections !== 'undefined' ? allConnections.length : 
                       $('.connection-item, .list-group-item[data-connection-id]').length,
                list: []
            },
            
            // Selected connection for editing
            selectedConnection: null,
            
            // New connection modal state
            newConnectionModal: {
                isOpen: $('#newConnectionModal').hasClass('show') || 
                        $('#newConnectionModal').is(':visible'),
                selectedType: $('#selected-connection-type').val() || null,
                selectedTypeName: null,
                activeCategory: $('.connection-tab.active').text().trim() || 'All'
            },
            
            // Connection types available
            connectionTypes: {
                categories: ['All', 'Database', 'API', 'Cloud', 'File'],
                selectedCategory: $('.connection-tab.active').text().trim() || 'All',
                availableTypes: []
            },
            
            // ODBC Drivers
            odbcDrivers: {
                available: [],
                count: $('#odbc_driver option, #new_odbc_driver option').length - 1 // Subtract default option
            },
            
            // Form state
            formState: {
                isEditing: $('#connectionForm').is(':visible') || 
                           $('input[name="connection_name"]').val() !== '',
                isCreatingNew: $('#newConnectionModal').hasClass('show'),
                hasUnsavedChanges: false
            },
            
            // Test connection status
            lastTestResult: null,
            
            // Available actions
            availableActions: []
        };
        
        // Get list of existing connections
        if (typeof allConnections !== 'undefined' && Array.isArray(allConnections)) {
            data.connections.list = allConnections.map(conn => ({
                id: conn.id,
                name: conn.connection_name,
                type: conn.database_type || conn.connection_type || 'Unknown',
                hasDataDictionary: conn.has_dictionary || false
            }));
        } else {
            // Try to extract from DOM
            $('.list-group-item[data-connection-id], .connection-item').each(function() {
                const name = $(this).find('.connection-name, .font-weight-bold').text().trim() ||
                            $(this).text().trim().split('\n')[0];
                if (name) {
                    data.connections.list.push({
                        name: name,
                        type: $(this).find('.badge, .connection-type').text().trim() || 'Database'
                    });
                }
            });
        }
        
        // Get selected connection type name
        if (data.newConnectionModal.selectedType && typeof connectionTypes !== 'undefined') {
            const typeInfo = connectionTypes[data.newConnectionModal.selectedType];
            if (typeInfo) {
                data.newConnectionModal.selectedTypeName = typeInfo.name;
            }
        }
        
        // Get available connection types
        if (typeof connectionTypes !== 'undefined') {
            data.connectionTypes.availableTypes = Object.entries(connectionTypes).map(([key, type]) => ({
                key: key,
                name: type.name,
                category: type.category,
                hasDriver: type.driverAvailable !== false
            }));
        }
        
        // Get ODBC drivers
        $('#new_odbc_driver option, #odbc_driver option').each(function() {
            const val = $(this).val();
            if (val && val !== '') {
                data.odbcDrivers.available.push(val);
            }
        });
        data.odbcDrivers.available = [...new Set(data.odbcDrivers.available)]; // Remove duplicates
        
        // Check for selected/editing connection
        const selectedId = $('input[name="connection_id"]').val() || 
                          $('.list-group-item.active').data('connection-id');
        if (selectedId && typeof allConnections !== 'undefined') {
            const conn = allConnections.find(c => c.id == selectedId);
            if (conn) {
                data.selectedConnection = {
                    id: conn.id,
                    name: conn.connection_name,
                    type: conn.database_type || conn.connection_type,
                    server: conn.server || '',
                    database: conn.database || '',
                    hasPassword: !!conn.password || conn.connection_string?.includes('Pwd='),
                    isConfigured: !!conn.connection_string
                };
            }
        }
        
        // Determine available actions
        if (data.newConnectionModal.isOpen) {
            if (!data.newConnectionModal.selectedType) {
                data.availableActions.push('Select a connection type from the grid');
            } else {
                data.availableActions.push('Fill in connection details');
                data.availableActions.push('Test the connection');
                data.availableActions.push('Save the connection');
            }
        } else if (data.selectedConnection) {
            data.availableActions.push('Edit connection settings');
            data.availableActions.push('Test connection');
            data.availableActions.push('Delete connection');
            data.availableActions.push('View data dictionary');
        } else {
            data.availableActions.push('Create new connection');
            data.availableActions.push('Select existing connection to edit');
        }
        
        // Form validation hints
        data.formValidation = {
            missingFields: []
        };
        
        if (data.newConnectionModal.isOpen || data.formState.isEditing) {
            // Check required fields
            const requiredFields = [
                { id: 'new_connection_name', label: 'Connection Name' },
                { id: 'new_server', label: 'Server' },
                { id: 'new_database', label: 'Database' },
                { id: 'new_username', label: 'Username' }
            ];
            
            requiredFields.forEach(field => {
                const el = document.getElementById(field.id);
                if (el && el.offsetParent !== null && !el.value) {
                    data.formValidation.missingFields.push(field.label);
                }
            });
        }
        
        return data;
    }
};

// Log that context is loaded
console.log('Connections assistant context loaded');
