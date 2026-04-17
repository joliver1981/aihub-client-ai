
window.assistantPageContext = {
    page: 'approvals',
    pageName: 'My Approvals',
    
    getPageData: function() {
        const data = {
            // Statistics
            statistics: {
                pending: 0,
                approved: 0,
                rejected: 0,
                overdue: 0
            },
            
            // Active filters
            filters: {
                status: 'all',
                workflow: null,
                dateRange: null,
                search: ''
            },
            
            // Approvals list
            approvals: {
                total: 0,
                displayed: [],
                selected: null
            },
            
            // User timezone
            timezone: null,
            
            // Detail panel state
            detailPanel: {
                isOpen: false,
                approvalId: null
            },
            
            // Available actions
            availableActions: []
        };
        
        // Get statistics from cards
        $('.stat-card').each(function() {
            const card = $(this);
            const iconClass = card.find('.stat-icon').attr('class') || '';
            const value = parseInt(card.find('h3, .stat-value').text()) || 0;
            
            if (iconClass.includes('pending')) {
                data.statistics.pending = value;
            } else if (iconClass.includes('approved')) {
                data.statistics.approved = value;
            } else if (iconClass.includes('rejected')) {
                data.statistics.rejected = value;
            } else if (iconClass.includes('overdue')) {
                data.statistics.overdue = value;
            }
        });
        
        // Get timezone indicator
        const tzIndicator = $('.timezone-indicator');
        if (tzIndicator.length) {
            data.timezone = tzIndicator.text().trim();
        }
        
        // Get active filters
        const statusFilter = $('#statusFilter, select[name="status"]');
        if (statusFilter.length) {
            data.filters.status = statusFilter.val() || 'all';
        }
        
        const workflowFilter = $('#workflowFilter, select[name="workflow"]');
        if (workflowFilter.length) {
            data.filters.workflow = workflowFilter.val() || null;
        }
        
        const searchInput = $('#searchApprovals, input[name="search"]');
        if (searchInput.length) {
            data.filters.search = searchInput.val() || '';
        }
        
        // Get approvals list
        const approvalRows = $('.approval-table tbody tr, .approval-item');
        data.approvals.total = approvalRows.length;
        
        approvalRows.slice(0, 10).each(function() {
            const row = $(this);
            const approval = {
                id: row.data('approval-id') || row.find('.approval-id').text().trim(),
                description: row.find('.approval-description, td:nth-child(1)').text().trim(),
                workflow: row.find('.workflow-name, td:nth-child(2)').text().trim(),
                submittedTime: row.find('.submitted-time, td:nth-child(3)').text().trim(),
                dueDate: row.find('.due-date, td:nth-child(4)').text().trim(),
                status: row.find('.status-badge, .badge').first().text().trim(),
                isOverdue: row.hasClass('overdue') || row.find('.overdue-indicator').length > 0
            };
            if (approval.description || approval.id) {
                data.approvals.displayed.push(approval);
            }
        });
        
        // Check for selected/open approval
        const selectedRow = approvalRows.filter('.selected, .active');
        if (selectedRow.length) {
            data.approvals.selected = selectedRow.data('approval-id');
            data.detailPanel.isOpen = true;
            data.detailPanel.approvalId = data.approvals.selected;
        }
        
        // Check if detail modal is open
        const detailModal = $('.approval-detail-modal.show, #approvalDetailModal.show');
        if (detailModal.length) {
            data.detailPanel.isOpen = true;
        }
        
        // Determine available actions
        if (data.detailPanel.isOpen) {
            data.availableActions.push('Review approval details');
            data.availableActions.push('Approve request');
            data.availableActions.push('Reject request');
            data.availableActions.push('Request more information');
            data.availableActions.push('Close detail panel');
        } else {
            if (data.statistics.pending > 0) {
                data.availableActions.push(`Review ${data.statistics.pending} pending approval(s)`);
            }
            if (data.statistics.overdue > 0) {
                data.availableActions.push(`Address ${data.statistics.overdue} overdue item(s)`);
            }
            data.availableActions.push('Filter approvals by status');
            data.availableActions.push('Filter by workflow');
            data.availableActions.push('Search for specific approval');
            data.availableActions.push('View approval history');
            
            if (data.approvals.total > 1) {
                data.availableActions.push('Select multiple for bulk action');
            }
        }
        
        // Priority message for overdue
        if (data.statistics.overdue > 0) {
            data.urgentMessage = `You have ${data.statistics.overdue} overdue approval(s) requiring immediate attention.`;
        }
        
        return data;
    }
};

console.log('Approvals assistant context loaded');