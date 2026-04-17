

window.assistantPageContext = {
    page: 'document_search',
    pageName: 'Document Search',
    
    getPageData: function() {
        const data = {
            // Active search tab
            activeTab: 'field', // field, attribute, language
            
            // Document type filter
            documentType: {
                selected: null,
                available: [],
                counts: {}
            },
            
            // Search options
            searchOptions: {
                maxResults: parseInt($('#max-results').val()) || 10,
                minScore: parseFloat($('#min-score').val()) || 0.5
            },
            
            // Field search criteria
            fieldSearch: {
                criteria: [],
                availableFields: []
            },
            
            // Language search
            languageSearch: {
                query: ''
            },
            
            // Search results
            results: {
                count: 0,
                displayed: [],
                hasResults: false
            },
            
            // Common fields
            commonFields: [],
            
            // Available actions
            availableActions: []
        };
        
        // Determine active tab
        const activeTabLink = $('.nav-tabs .nav-link.active, .nav-pills .nav-link.active');
        if (activeTabLink.length) {
            const tabText = activeTabLink.text().trim().toLowerCase();
            if (tabText.includes('attribute')) {
                data.activeTab = 'attribute';
            } else if (tabText.includes('language')) {
                data.activeTab = 'language';
            } else {
                data.activeTab = 'field';
            }
        }
        
        // Get selected document type
        const selectedType = $('#document-type-list .list-group-item.active, .doc-type-filter.active');
        if (selectedType.length) {
            const typeText = selectedType.text().trim().split(/\s+/)[0];
            data.documentType.selected = typeText === 'All' ? null : typeText;
        }
        
        // Get available document types
        $('#document-type-list .list-group-item').each(function() {
            const item = $(this);
            const name = item.text().trim().replace(/\d+$/, '').trim();
            const count = parseInt(item.find('.badge').text()) || 0;
            if (name && name !== 'All Documents') {
                data.documentType.available.push(name);
                data.documentType.counts[name] = count;
            }
        });
        
        // Get field search criteria
        $('.field-criteria-row').each(function() {
            const row = $(this);
            const criteria = {
                field: row.find('.field-select-dropdown, select[name="field_name[]"]').val() || '',
                operator: row.find('.operator-dropdown, select[name="field_operator[]"]').val() || 'equals',
                value: row.find('input[name="field_value[]"]').val() || ''
            };
            if (criteria.field || criteria.value) {
                data.fieldSearch.criteria.push(criteria);
            }
        });
        
        // Get available fields
        $('.field-select-dropdown option, #field-select option').each(function() {
            const val = $(this).val();
            const text = $(this).text().trim();
            if (val && val !== '%' && text) {
                data.fieldSearch.availableFields.push({
                    path: val,
                    name: text
                });
            }
        });
        
        // Get language search query
        const languageInput = $('#language-query, #search-query, input[name="query"]');
        if (languageInput.length) {
            data.languageSearch.query = languageInput.val() || '';
        }
        
        // Get common fields
        $('#collapseCommonFields .field-select, .common-field-item').each(function() {
            const fieldName = $(this).data('field') || $(this).text().trim();
            if (fieldName) {
                data.commonFields.push(fieldName);
            }
        });
        
        // Count results
        const resultItems = $('.search-result, .result-item, #search-results .card');
        data.results.count = resultItems.length;
        data.results.hasResults = data.results.count > 0;
        
        // Get displayed results (limited)
        resultItems.slice(0, 5).each(function() {
            const item = $(this);
            const result = {
                name: item.find('.result-name, .document-name, .card-title').text().trim(),
                type: item.find('.result-type, .badge').first().text().trim(),
                score: parseFloat(item.find('.result-score, .score').text()) || null,
                preview: item.find('.result-preview, .preview-text').text().trim().substring(0, 100)
            };
            if (result.name) {
                data.results.displayed.push(result);
            }
        });
        
        // Determine available actions based on state
        data.availableActions.push('Switch search mode (Field/Attribute/Language)');
        data.availableActions.push('Filter by document type');
        data.availableActions.push('Adjust result settings');
        
        if (data.activeTab === 'field') {
            data.availableActions.push('Add field criteria');
            data.availableActions.push('Select field to search');
            data.availableActions.push('Choose operator (equals, contains, etc.)');
        } else if (data.activeTab === 'language') {
            data.availableActions.push('Enter natural language query');
            data.availableActions.push('Ask questions about documents');
        }
        
        data.availableActions.push('Execute search');
        
        if (data.results.hasResults) {
            data.availableActions.push('View document details');
            data.availableActions.push('Refine search criteria');
            data.availableActions.push('Export results');
        }
        
        return data;
    }
};

console.log('Document Search assistant context loaded');