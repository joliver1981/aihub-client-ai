/**
 * Page Context: Email Processing History
 * 
 * Provides real-time context about the email processing history page.
 * Add this script to email_processing_history.html
 * 
 * Extracts:
 * - Dispatcher status (running/stopped)
 * - Processing statistics
 * - Current filters
 * - Table data summary
 * - Pagination state
 * - Modal state
 */

window.assistantPageContext = {
    page: 'email_processing_history',
    pageName: 'Email Processing History',
    
    getPageData: function() {
        const data = {
            // Dispatcher status
            dispatcher: {
                isRunning: false,
                statusText: '',
                totalProcessed: 0
            },
            
            // Statistics
            statistics: {
                totalProcessed: 0,
                completed: 0,
                failed: 0,
                avgProcessingTime: ''
            },
            
            // Agent breakdown
            agentBreakdown: {
                visible: false,
                agentCount: 0,
                agents: []
            },
            
            // Current filters
            filters: {
                agent: {
                    value: '',
                    label: 'All Agents'
                },
                status: {
                    value: '',
                    label: 'All Statuses'
                },
                type: {
                    value: '',
                    label: 'All Types'
                },
                timePeriod: {
                    value: '7',
                    label: 'Last 7 Days'
                }
            },
            
            // Table state
            table: {
                isLoading: false,
                hasRecords: false,
                recordCount: 0,
                hasFailedRecords: false,
                failedCount: 0
            },
            
            // Pagination
            pagination: {
                currentPage: 1,
                totalRecords: 0,
                pageSize: 50,
                hasPrevious: false,
                hasNext: false,
                showingText: ''
            },
            
            // Modal state
            modal: {
                errorModalOpen: false
            },
            
            // Available actions
            availableActions: []
        };
        
        // === DISPATCHER STATUS ===
        const dispatcherDot = document.getElementById('dispatcherDot');
        if (dispatcherDot) {
            data.dispatcher.isRunning = dispatcherDot.classList.contains('running');
        }
        
        const dispatcherStatus = document.getElementById('dispatcherStatus');
        if (dispatcherStatus) {
            data.dispatcher.statusText = dispatcherStatus.textContent.trim();
            // Extract processed count from status text like "Running (42 processed)"
            const match = data.dispatcher.statusText.match(/\((\d+)\s+processed\)/);
            if (match) {
                data.dispatcher.totalProcessed = parseInt(match[1]) || 0;
            }
        }
        
        // === STATISTICS ===
        const statTotal = document.getElementById('statTotal');
        if (statTotal) {
            data.statistics.totalProcessed = parseInt(statTotal.textContent) || 0;
        }
        
        const statCompleted = document.getElementById('statCompleted');
        if (statCompleted) {
            data.statistics.completed = parseInt(statCompleted.textContent) || 0;
        }
        
        const statFailed = document.getElementById('statFailed');
        if (statFailed) {
            data.statistics.failed = parseInt(statFailed.textContent) || 0;
        }
        
        const statAvgTime = document.getElementById('statAvgTime');
        if (statAvgTime) {
            data.statistics.avgProcessingTime = statAvgTime.textContent.trim();
        }
        
        // === AGENT BREAKDOWN ===
        const agentBreakdown = document.getElementById('agentBreakdown');
        if (agentBreakdown) {
            data.agentBreakdown.visible = agentBreakdown.style.display !== 'none';
            
            const agentBars = document.querySelectorAll('#agentBars .agent-bar');
            data.agentBreakdown.agentCount = agentBars.length;
            
            agentBars.forEach(bar => {
                const nameEl = bar.querySelector('.agent-name');
                const countEl = bar.querySelector('.count');
                if (nameEl && countEl) {
                    data.agentBreakdown.agents.push({
                        name: nameEl.textContent.trim(),
                        count: parseInt(countEl.textContent) || 0
                    });
                }
            });
        }
        
        // === FILTERS ===
        const filterAgent = document.getElementById('filterAgent');
        if (filterAgent) {
            data.filters.agent.value = filterAgent.value;
            const selectedOption = filterAgent.options[filterAgent.selectedIndex];
            data.filters.agent.label = selectedOption ? selectedOption.text : 'All Agents';
        }
        
        const filterStatus = document.getElementById('filterStatus');
        if (filterStatus) {
            data.filters.status.value = filterStatus.value;
            const selectedOption = filterStatus.options[filterStatus.selectedIndex];
            data.filters.status.label = selectedOption ? selectedOption.text : 'All Statuses';
        }
        
        const filterType = document.getElementById('filterType');
        if (filterType) {
            data.filters.type.value = filterType.value;
            const selectedOption = filterType.options[filterType.selectedIndex];
            data.filters.type.label = selectedOption ? selectedOption.text : 'All Types';
        }
        
        const filterDays = document.getElementById('filterDays');
        if (filterDays) {
            data.filters.timePeriod.value = filterDays.value;
            const selectedOption = filterDays.options[filterDays.selectedIndex];
            data.filters.timePeriod.label = selectedOption ? selectedOption.text : 'Last 7 Days';
        }
        
        // === TABLE STATE ===
        const tbody = document.getElementById('historyTableBody');
        if (tbody) {
            const loadingState = tbody.querySelector('.fa-spinner');
            data.table.isLoading = !!loadingState;
            
            const rows = tbody.querySelectorAll('tr:not(:has(.empty-state))');
            data.table.recordCount = rows.length;
            data.table.hasRecords = rows.length > 0 && !loadingState;
            
            // Count failed records visible
            const failedBadges = tbody.querySelectorAll('.status-badge.failed');
            data.table.failedCount = failedBadges.length;
            data.table.hasFailedRecords = failedBadges.length > 0;
        }
        
        // === PAGINATION ===
        const paginationInfo = document.getElementById('paginationInfo');
        if (paginationInfo) {
            data.pagination.showingText = paginationInfo.textContent.trim();
            // Parse "Showing 1-50 of 150 records"
            const match = data.pagination.showingText.match(/Showing (\d+)-(\d+) of (\d+)/);
            if (match) {
                const start = parseInt(match[1]);
                const end = parseInt(match[2]);
                const total = parseInt(match[3]);
                data.pagination.totalRecords = total;
                data.pagination.currentPage = Math.ceil(start / data.pagination.pageSize);
            }
        }
        
        const prevBtn = document.getElementById('prevBtn');
        if (prevBtn) {
            data.pagination.hasPrevious = !prevBtn.disabled;
        }
        
        const nextBtn = document.getElementById('nextBtn');
        if (nextBtn) {
            data.pagination.hasNext = !nextBtn.disabled;
        }
        
        // === MODAL STATE ===
        const errorModal = document.getElementById('errorModal');
        if (errorModal && errorModal.classList.contains('show')) {
            data.modal.errorModalOpen = true;
        }
        
        // === AVAILABLE ACTIONS ===
        if (data.table.isLoading) {
            data.availableActions = [
                'Waiting for data to load...'
            ];
        } else if (data.modal.errorModalOpen) {
            data.availableActions = [
                'View error details',
                'Click Retry Processing to retry the failed email',
                'Click Close to dismiss'
            ];
        } else if (!data.table.hasRecords) {
            data.availableActions = [
                'No processing records found for current filters',
                'Adjust filters to see more records',
                'Click Refresh to reload data'
            ];
        } else {
            data.availableActions = [
                'View processing history records',
                'Filter by agent, status, type, or time period',
                'Click agent name to view agent-specific history',
                'Click Refresh to reload data'
            ];
            
            if (data.table.hasFailedRecords) {
                data.availableActions.push('Click error icon to view failure details and retry');
            }
            
            if (data.dispatcher.isRunning) {
                data.availableActions.push('Stop the email dispatcher');
            } else {
                data.availableActions.push('Start the email dispatcher');
            }
            
            if (data.pagination.hasPrevious) {
                data.availableActions.push('Go to previous page');
            }
            if (data.pagination.hasNext) {
                data.availableActions.push('Go to next page');
            }
        }
        
        // Debug summary
        console.log('=== Email Processing History Context ===');
        console.log('Dispatcher:', data.dispatcher.isRunning ? 'Running' : 'Stopped');
        console.log('Stats - Total:', data.statistics.totalProcessed,
                   '| Completed:', data.statistics.completed,
                   '| Failed:', data.statistics.failed);
        console.log('Filters - Agent:', data.filters.agent.label,
                   '| Status:', data.filters.status.label,
                   '| Type:', data.filters.type.label,
                   '| Period:', data.filters.timePeriod.label);
        console.log('Table - Records:', data.table.recordCount,
                   '| Failed visible:', data.table.failedCount);
        console.log('Pagination:', data.pagination.showingText);
        
        return data;
    }
};

console.log('Email Processing History context loaded');
