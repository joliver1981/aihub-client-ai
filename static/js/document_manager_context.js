

window.assistantPageContext = {
    page: 'document_manager',
    pageName: 'Document Manager',
    
    getPageData: function() {
        const data = {
            // Statistics
            statistics: {
                totalDocuments: parseInt($('#totalDocuments').text()) || 0,
                totalPages: parseInt($('#totalPages').text()) || 0,
                documentTypes: parseInt($('#totalTypes').text()) || 0,
                lastUpdated: $('#lastUpdated').text().trim() || 'Unknown'
            },
            
            // Active filters
            filters: {
                documentType: $('#filterDocType').val() || 'all',
                dateRange: $('#filterDateRange').val() || 'all',
                searchQuery: $('#searchDocuments').val() || '',
                customDateStart: $('#startDate').val() || null,
                customDateEnd: $('#endDate').val() || null
            },
            
            // Document list
            documents: {
                displayedCount: 0,
                selectedCount: 0,
                list: []
            },
            
            // Available document types
            availableTypes: [],
            
            // Modal states
            modals: {
                reprocessOpen: $('#reprocessVectorModal').hasClass('show') || 
                              $('#reprocessVectorModal').is(':visible'),
                reprocessMode: null
            },
            
            // Vector database status
            vectorDb: {
                lastReprocess: null,
                isProcessing: false
            },
            
            // Available actions
            availableActions: []
        };
        
        // Get available document types
        $('#filterDocType option').each(function() {
            const val = $(this).val();
            const text = $(this).text().trim();
            if (val && val !== '') {
                data.availableTypes.push({
                    value: val,
                    label: text
                });
            }
        });
        
        // Count displayed documents
        const docRows = $('table tbody tr, .document-item, .document-row');
        data.documents.displayedCount = docRows.length;
        
        // Count selected documents
        data.documents.selectedCount = docRows.filter('.selected, :has(input:checked)').length;
        
        // Get document list (limited)
        docRows.slice(0, 10).each(function() {
            const row = $(this);
            const doc = {
                name: row.find('.doc-name, td:first').text().trim(),
                type: row.find('.doc-type, .badge').first().text().trim(),
                status: row.find('.status-badge, .doc-status').text().trim() || 'Unknown',
                pageCount: parseInt(row.find('.page-count, td:nth-child(3)').text()) || 0
            };
            if (doc.name) {
                data.documents.list.push(doc);
            }
        });
        
        // Check if processing is happening
        data.vectorDb.isProcessing = $('.processing-indicator, .spinner-border:visible').length > 0;
        
        // Determine available actions
        if (data.modals.reprocessOpen) {
            data.availableActions.push('Select reprocessing options');
            data.availableActions.push('Start reprocessing');
            data.availableActions.push('Cancel reprocessing');
        } else {
            data.availableActions.push('Filter documents by type');
            data.availableActions.push('Filter by date range');
            data.availableActions.push('Search for documents');
            data.availableActions.push('View document details');
            
            if (data.documents.displayedCount > 0) {
                data.availableActions.push('Select documents for bulk actions');
                data.availableActions.push('Reprocess selected vectors');
            }
            
            data.availableActions.push('Force rebuild all vectors');
        }
        
        // Add context about current filter state
        if (data.filters.documentType && data.filters.documentType !== 'all') {
            data.activeFilterDescription = `Filtered by type: ${data.filters.documentType}`;
        }
        if (data.filters.searchQuery) {
            data.activeFilterDescription = (data.activeFilterDescription || '') + 
                ` | Search: "${data.filters.searchQuery}"`;
        }
        
        return data;
    }
};

console.log('Document Manager assistant context loaded');