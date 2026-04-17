/**
 * Page Context: Universal Integrations
 * 
 * Provides real-time context about the integrations page.
 * Add this script to integrations.html
 * 
 * Extracts:
 * - Current tab and view state
 * - Connected integrations list
 * - Template gallery state
 * - Modal form data
 * - Operation test results
 */

window.assistantPageContext = {
    page: 'integrations',
    pageName: 'Universal Integrations',
    
    // Dynamic context - called each time user sends a message
    getPageData: function() {
        // Determine current tab
        var activeTab = getActiveTab();
        
        // Get connected integrations
        var connectedIntegrations = getConnectedIntegrations();
        
        // Get template gallery state
        var galleryState = getGalleryState();
        
        // Get modal state if any modal is open
        var modalState = getModalState();
        
        // Get any error messages displayed
        var errorMessages = getErrorMessages();
        
        // Get operation test results if visible
        var testResults = getTestResults();
        
        // Generate contextual hints
        var hints = getAssistantHints(activeTab, connectedIntegrations, modalState, errorMessages);
        
        return {
            // Current view
            activeTab: activeTab,
            
            // Gallery tab state
            gallerySearchTerm: galleryState.searchTerm,
            gallerySelectedCategory: galleryState.selectedCategory,
            galleryVisibleTemplates: galleryState.visibleTemplates,
            galleryTemplateCount: galleryState.templateCount,
            
            // My Integrations tab state
            connectedIntegrationCount: connectedIntegrations.length,
            connectedIntegrations: connectedIntegrations.slice(0, 15), // Limit payload
            
            // Modal state
            modalOpen: modalState.isOpen,
            modalType: modalState.type,
            modalData: modalState.data,
            
            // Operation testing
            testResults: testResults,
            
            // Error state
            hasErrors: errorMessages.length > 0,
            errorMessages: errorMessages,
            
            // Hints for the assistant
            hints: hints,
            
            // Available actions based on current state
            availableActions: getAvailableActions(activeTab, modalState, connectedIntegrations)
        };
    }
};

/**
 * Determine which tab is currently active
 */
function getActiveTab() {
    var galleryTab = document.querySelector('[data-tab="gallery"].active, [href="#gallery"].active, #gallery-tab.active');
    var myIntegrationsTab = document.querySelector('[data-tab="my-integrations"].active, [href="#my-integrations"].active, #my-integrations-tab.active');
    
    // Check by visible content if tabs don't have active class
    var galleryContent = document.getElementById('gallery');
    var myIntegrationsContent = document.getElementById('my-integrations');
    
    if (galleryTab || (galleryContent && galleryContent.classList.contains('show'))) {
        return 'gallery';
    }
    if (myIntegrationsTab || (myIntegrationsContent && myIntegrationsContent.classList.contains('show'))) {
        return 'my-integrations';
    }
    
    // Default to gallery
    return 'gallery';
}

/**
 * Get list of connected integrations from the My Integrations tab
 */
function getConnectedIntegrations() {
    var integrations = [];
    
    // Look for integration cards/rows in the my-integrations section
    var integrationItems = document.querySelectorAll('#my-integrations .integration-item, #my-integrations .integration-card, .connected-integration');
    
    integrationItems.forEach(function(item) {
        var integration = {
            id: item.dataset.integrationId || item.dataset.id || '',
            name: '',
            platform: '',
            status: 'connected',
            lastUsed: '',
            requestCount: ''
        };
        
        // Extract name
        var nameEl = item.querySelector('.integration-name, .card-title, h5, h6');
        if (nameEl) {
            integration.name = nameEl.textContent.trim();
        }
        
        // Extract platform
        var platformEl = item.querySelector('.platform-name, .badge, small');
        if (platformEl) {
            integration.platform = platformEl.textContent.trim();
        }
        
        // Extract status
        if (item.classList.contains('disconnected') || item.querySelector('.status-disconnected')) {
            integration.status = 'disconnected';
        }
        
        // Extract stats if available
        var statsEl = item.querySelector('.integration-stats, .stats');
        if (statsEl) {
            var requestMatch = statsEl.textContent.match(/(\d+)\s*requests?/i);
            if (requestMatch) {
                integration.requestCount = requestMatch[1];
            }
        }
        
        if (integration.name || integration.id) {
            integrations.push(integration);
        }
    });
    
    return integrations;
}

/**
 * Get template gallery state
 */
function getGalleryState() {
    var state = {
        searchTerm: '',
        selectedCategory: 'all',
        visibleTemplates: [],
        templateCount: 0
    };
    
    // Get search term
    var searchInput = document.querySelector('#gallery-search, #template-search, input[type="search"]');
    if (searchInput) {
        state.searchTerm = searchInput.value || '';
    }
    
    // Get selected category
    var activeCategoryPill = document.querySelector('.category-pill.active, .category-filter.active, [data-category].active');
    if (activeCategoryPill) {
        state.selectedCategory = activeCategoryPill.dataset.category || activeCategoryPill.textContent.trim();
    }
    
    // Get visible templates
    var templateCards = document.querySelectorAll('#gallery .template-card:not(.d-none), #gallery .integration-template:not(.d-none), .template-item:not([style*="display: none"])');
    
    templateCards.forEach(function(card) {
        var template = {
            key: card.dataset.templateKey || card.dataset.key || '',
            name: '',
            category: '',
            authType: '',
            isConnected: false
        };
        
        // Extract name
        var nameEl = card.querySelector('.template-name, .card-title, h5, h6');
        if (nameEl) {
            template.name = nameEl.textContent.trim();
        }
        
        // Extract category
        var categoryEl = card.querySelector('.template-category, .category-badge');
        if (categoryEl) {
            template.category = categoryEl.textContent.trim();
        }
        
        // Check if connected
        if (card.classList.contains('connected') || card.querySelector('.connected-badge, .badge-success')) {
            template.isConnected = true;
        }
        
        // Extract auth type
        var authEl = card.querySelector('.auth-type, [data-auth-type]');
        if (authEl) {
            template.authType = authEl.dataset.authType || authEl.textContent.trim();
        }
        
        if (template.name || template.key) {
            state.visibleTemplates.push(template);
        }
    });
    
    state.templateCount = state.visibleTemplates.length;
    
    return state;
}

/**
 * Get current modal state
 */
function getModalState() {
    var state = {
        isOpen: false,
        type: null,
        data: {}
    };
    
    // Check for setup modal
    var setupModal = document.querySelector('#setup-modal.show, #integrationSetupModal.show, .setup-modal.show');
    if (setupModal) {
        state.isOpen = true;
        state.type = 'setup';
        state.data = extractSetupModalData(setupModal);
        return state;
    }
    
    // Check for details modal
    var detailsModal = document.querySelector('#details-modal.show, #integrationDetailsModal.show, .details-modal.show');
    if (detailsModal) {
        state.isOpen = true;
        state.type = 'details';
        state.data = extractDetailsModalData(detailsModal);
        return state;
    }
    
    // Check for custom template modal
    var customModal = document.querySelector('#custom-template-modal.show, #customTemplateModal.show');
    if (customModal) {
        state.isOpen = true;
        state.type = 'custom-template';
        state.data = extractCustomTemplateModalData(customModal);
        return state;
    }
    
    // Check for any generic open modal
    var anyModal = document.querySelector('.modal.show');
    if (anyModal) {
        state.isOpen = true;
        state.type = 'unknown';
        state.data = {
            modalId: anyModal.id,
            title: anyModal.querySelector('.modal-title')?.textContent?.trim() || ''
        };
    }
    
    return state;
}

/**
 * Extract data from setup modal
 */
function extractSetupModalData(modal) {
    var data = {
        templateName: '',
        templateKey: '',
        authType: '',
        integrationName: '',
        hasCredentials: false,
        instanceConfig: {},
        validationErrors: []
    };
    
    // Template info
    var templateTitle = modal.querySelector('.template-title, .modal-title');
    if (templateTitle) {
        data.templateName = templateTitle.textContent.trim();
    }
    
    data.templateKey = modal.dataset.templateKey || '';
    
    // Auth type
    var authTypeEl = modal.querySelector('[data-auth-type], .auth-type-badge');
    if (authTypeEl) {
        data.authType = authTypeEl.dataset.authType || authTypeEl.textContent.trim();
    }
    
    // Integration name input
    var nameInput = modal.querySelector('input[name="integrationName"], input[name="name"], #integration-name');
    if (nameInput) {
        data.integrationName = nameInput.value || '';
    }
    
    // Check for credentials fields
    var credentialFields = modal.querySelectorAll('input[type="password"], input[name*="api_key"], input[name*="token"], input[name*="secret"]');
    data.hasCredentials = credentialFields.length > 0;
    
    // Check which credential fields have values
    var filledCredentials = [];
    credentialFields.forEach(function(field) {
        if (field.value) {
            filledCredentials.push(field.name || field.id);
        }
    });
    data.filledCredentialFields = filledCredentials;
    
    // Instance config fields
    var configFields = modal.querySelectorAll('.instance-config input, .instance-config select');
    configFields.forEach(function(field) {
        var name = field.name || field.id;
        if (name && !name.includes('password') && !name.includes('secret') && !name.includes('token') && !name.includes('api_key')) {
            data.instanceConfig[name] = field.value || '';
        }
    });
    
    // Validation errors
    var errorEls = modal.querySelectorAll('.is-invalid, .invalid-feedback:not(:empty), .error-message');
    errorEls.forEach(function(el) {
        var errorText = el.classList.contains('is-invalid') 
            ? el.nextElementSibling?.textContent?.trim()
            : el.textContent.trim();
        if (errorText) {
            data.validationErrors.push(errorText);
        }
    });
    
    return data;
}

/**
 * Extract data from details modal
 */
function extractDetailsModalData(modal) {
    var data = {
        integrationId: '',
        integrationName: '',
        platformName: '',
        selectedOperation: '',
        availableOperations: [],
        parameters: {},
        lastTestResult: null
    };
    
    data.integrationId = modal.dataset.integrationId || '';
    
    // Integration name
    var nameEl = modal.querySelector('.integration-name, .modal-title');
    if (nameEl) {
        data.integrationName = nameEl.textContent.trim();
    }
    
    // Platform name
    var platformEl = modal.querySelector('.platform-name, .platform-badge');
    if (platformEl) {
        data.platformName = platformEl.textContent.trim();
    }
    
    // Selected operation
    var operationSelect = modal.querySelector('#operation-selector, select[name="operation"]');
    if (operationSelect) {
        data.selectedOperation = operationSelect.value || '';
        
        // Get all available operations
        var options = operationSelect.querySelectorAll('option');
        options.forEach(function(opt) {
            if (opt.value) {
                data.availableOperations.push({
                    key: opt.value,
                    name: opt.textContent.trim()
                });
            }
        });
    }
    
    // Parameters
    var paramInputs = modal.querySelectorAll('.operation-params input, .operation-params select, .operation-params textarea');
    paramInputs.forEach(function(input) {
        var name = input.name || input.dataset.param || input.id;
        if (name) {
            data.parameters[name] = input.value || '';
        }
    });
    
    // Test result
    var resultEl = modal.querySelector('.test-result, #operation-result, .result-container');
    if (resultEl && resultEl.textContent.trim()) {
        data.lastTestResult = {
            visible: true,
            isSuccess: resultEl.classList.contains('success') || resultEl.classList.contains('text-success'),
            isError: resultEl.classList.contains('error') || resultEl.classList.contains('text-danger'),
            preview: resultEl.textContent.trim().substring(0, 500)
        };
    }
    
    return data;
}

/**
 * Extract data from custom template modal
 */
function extractCustomTemplateModalData(modal) {
    var data = {
        templateKey: '',
        platformName: '',
        baseUrl: '',
        authType: '',
        operationCount: 0,
        isValid: true
    };
    
    // Template key
    var keyInput = modal.querySelector('input[name="templateKey"], #template-key');
    if (keyInput) {
        data.templateKey = keyInput.value || '';
    }
    
    // Platform name
    var nameInput = modal.querySelector('input[name="platformName"], #platform-name');
    if (nameInput) {
        data.platformName = nameInput.value || '';
    }
    
    // Base URL
    var urlInput = modal.querySelector('input[name="baseUrl"], #base-url');
    if (urlInput) {
        data.baseUrl = urlInput.value || '';
    }
    
    // Auth type
    var authSelect = modal.querySelector('select[name="authType"], #auth-type');
    if (authSelect) {
        data.authType = authSelect.value || '';
    }
    
    // Count operations
    var operationItems = modal.querySelectorAll('.operation-item, .operation-row');
    data.operationCount = operationItems.length;
    
    // Check validity
    var invalidFields = modal.querySelectorAll('.is-invalid');
    data.isValid = invalidFields.length === 0;
    
    return data;
}

/**
 * Get any error messages displayed on the page
 */
function getErrorMessages() {
    var errors = [];
    
    // Alert boxes
    var alerts = document.querySelectorAll('.alert-danger, .alert-warning, .error-alert');
    alerts.forEach(function(alert) {
        if (alert.offsetParent !== null) { // Check if visible
            errors.push(alert.textContent.trim());
        }
    });
    
    // Toast notifications
    var toasts = document.querySelectorAll('.toast.show .toast-body');
    toasts.forEach(function(toast) {
        var text = toast.textContent.trim();
        if (text.toLowerCase().includes('error') || text.toLowerCase().includes('failed')) {
            errors.push(text);
        }
    });
    
    return errors;
}

/**
 * Get operation test results
 */
function getTestResults() {
    var results = {
        hasResults: false,
        isSuccess: null,
        statusCode: null,
        responseTime: null,
        dataPreview: ''
    };
    
    var resultContainer = document.querySelector('.test-result, #operation-result, .result-container');
    if (!resultContainer || !resultContainer.textContent.trim()) {
        return results;
    }
    
    results.hasResults = true;
    
    // Check success/failure
    if (resultContainer.classList.contains('success') || resultContainer.classList.contains('text-success') || resultContainer.classList.contains('border-success')) {
        results.isSuccess = true;
    } else if (resultContainer.classList.contains('error') || resultContainer.classList.contains('text-danger') || resultContainer.classList.contains('border-danger')) {
        results.isSuccess = false;
    }
    
    // Try to extract status code
    var statusMatch = resultContainer.textContent.match(/status[:\s]*(\d{3})/i);
    if (statusMatch) {
        results.statusCode = parseInt(statusMatch[1]);
    }
    
    // Try to extract response time
    var timeMatch = resultContainer.textContent.match(/(\d+)\s*ms/i);
    if (timeMatch) {
        results.responseTime = parseInt(timeMatch[1]);
    }
    
    // Get preview of data
    results.dataPreview = resultContainer.textContent.trim().substring(0, 500);
    
    return results;
}

/**
 * Generate contextual hints for the assistant
 */
function getAssistantHints(activeTab, connectedIntegrations, modalState, errorMessages) {
    var hints = [];
    
    // Tab-based hints
    if (activeTab === 'gallery' && connectedIntegrations.length === 0) {
        hints.push('No integrations connected yet - user may need help choosing and setting up their first integration');
    }
    
    // Modal-based hints
    if (modalState.isOpen) {
        if (modalState.type === 'setup') {
            var setupData = modalState.data;
            
            if (!setupData.integrationName) {
                hints.push('Integration name not entered yet');
            }
            
            if (setupData.authType === 'oauth2') {
                hints.push('This is an OAuth2 integration - will require authorization flow');
            }
            
            if (setupData.authType === 'api_key' && setupData.filledCredentialFields?.length === 0) {
                hints.push('API key credentials not entered - user needs to get API key from the service');
            }
            
            if (setupData.validationErrors.length > 0) {
                hints.push('Form has validation errors: ' + setupData.validationErrors.join(', '));
            }
        }
        
        if (modalState.type === 'details') {
            var detailsData = modalState.data;
            
            if (!detailsData.selectedOperation) {
                hints.push('No operation selected - user needs to choose an operation to test');
            }
            
            if (detailsData.lastTestResult?.isError) {
                hints.push('Last operation test failed - user may need help troubleshooting');
            }
        }
        
        if (modalState.type === 'custom-template') {
            var customData = modalState.data;
            
            if (!customData.baseUrl) {
                hints.push('Base URL not specified for custom integration');
            }
            
            if (customData.operationCount === 0) {
                hints.push('No operations defined for custom integration');
            }
        }
    }
    
    // Error-based hints
    if (errorMessages.length > 0) {
        hints.push('Page showing errors - user likely needs troubleshooting help');
    }
    
    // Connection status hints
    var disconnectedCount = connectedIntegrations.filter(function(i) { return i.status === 'disconnected'; }).length;
    if (disconnectedCount > 0) {
        hints.push(disconnectedCount + ' integration(s) showing as disconnected - may need reconnection');
    }
    
    return hints;
}

/**
 * Get available actions based on current state
 */
function getAvailableActions(activeTab, modalState, connectedIntegrations) {
    var actions = [];
    
    if (modalState.isOpen) {
        // Modal-specific actions
        switch (modalState.type) {
            case 'setup':
                actions.push('Fill in integration details');
                actions.push('Enter credentials');
                if (modalState.data.authType === 'oauth2') {
                    actions.push('Start OAuth authorization');
                }
                actions.push('Connect integration');
                actions.push('Cancel setup');
                break;
                
            case 'details':
                actions.push('Select an operation');
                actions.push('Enter operation parameters');
                actions.push('Test operation');
                actions.push('View execution logs');
                actions.push('Delete integration');
                actions.push('Close details');
                break;
                
            case 'custom-template':
                actions.push('Define template settings');
                actions.push('Add operations');
                actions.push('Configure authentication');
                actions.push('Save custom template');
                break;
        }
    } else {
        // Tab-specific actions
        if (activeTab === 'gallery') {
            actions.push('Browse integration templates');
            actions.push('Search for a template');
            actions.push('Filter by category');
            actions.push('Select a template to connect');
            actions.push('Create custom integration');
        } else {
            actions.push('View connected integrations');
            actions.push('Manage an integration');
            actions.push('Test an integration');
            actions.push('Disconnect an integration');
        }
        
        // Always available
        actions.push('Switch tabs');
        if (connectedIntegrations.length > 0) {
            actions.push('Use integration in workflow');
        }
    }
    
    return actions;
}

console.log('Universal Integrations context loaded');
