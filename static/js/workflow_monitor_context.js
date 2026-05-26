/**
 * Page context provider for /workflow_monitor (templates/workflow_monitor.html).
 * The page is tab-based (Dashboard, Executions, Approvals, Analytics, Schedules,
 * Logs); we report which tab is active and surface the data visible on that tab.
 */
window.assistantPageContext = {
    page: 'workflow_monitor',
    pageName: 'Workflow Monitor',

    getPageData: function () {
        const data = {
            activeTab: '',
            stats: {
                activeWorkflows: 0,
                pausedWorkflows: 0,
                completedToday: 0,
                failedToday: 0,
                pendingApprovals: 0
            },
            recentExecutions: { rowCount: 0, rows: [] },
            executionsList: {
                rowCount: 0,
                rows: [],
                statusFilter: '',
                countLabel: ''
            },
            approvals: {
                rowCount: 0,
                rows: [],
                filter: 'pending'
            },
            analytics: {
                dateRange: '',
                workflow: '',
                customRangeVisible: false
            },
            ui: { theme: 'dark' },
            availableActions: []
        };

        const activeNav = document.querySelector('.nav-pills .nav-link.active');
        if (activeNav) {
            data.activeTab = (activeNav.textContent || '').trim().split('\n')[0]
                .replace(/\s+\d+$/, '');  // strip trailing badge count like "Approvals 3"
        }

        const num = function (id) {
            const el = document.getElementById(id);
            if (!el) return 0;
            const n = parseInt((el.textContent || '0').replace(/[^0-9-]/g, ''), 10);
            return isNaN(n) ? 0 : n;
        };
        data.stats.activeWorkflows = num('active-workflows-count');
        data.stats.pausedWorkflows = num('paused-workflows-count');
        data.stats.completedToday = num('completed-workflows-count');
        data.stats.failedToday = num('failed-workflows-count');
        data.stats.pendingApprovals = num('approval-count');

        const captureTableRows = function (tbodyId, target, columnNames) {
            const tbody = document.getElementById(tbodyId);
            if (!tbody) return;
            const rows = tbody.querySelectorAll('tr');
            rows.forEach(function (tr) {
                const cells = tr.querySelectorAll('td');
                if (cells.length === 0 || cells.length === 1) return;  // skip "Loading…" placeholder
                const row = {};
                cells.forEach(function (td, i) {
                    const key = columnNames[i] || ('col' + i);
                    row[key] = (td.textContent || '').trim().split('\n')[0].slice(0, 200);
                });
                target.rows.push(row);
            });
            target.rowCount = target.rows.length;
        };

        captureTableRows('recent-executions-table', data.recentExecutions,
            ['id', 'workflow', 'status', 'started', 'duration', 'actions']);
        captureTableRows('executions-table-body', data.executionsList,
            ['id', 'workflow', 'started', 'completed', 'status', 'initiatedBy', 'actions']);
        captureTableRows('approvals-table', data.approvals,
            ['workflow', 'title', 'requested', 'status', 'assignedTo', 'actions']);
        // Limit row payload to keep prompts small
        const cap = function (t) {
            if (t.rows.length > 15) t.rows = t.rows.slice(0, 15);
        };
        cap(data.recentExecutions); cap(data.executionsList); cap(data.approvals);

        const statusFilter = document.getElementById('status-filter');
        if (statusFilter) data.executionsList.statusFilter = statusFilter.value || '';
        const execCount = document.getElementById('executions-count');
        if (execCount) data.executionsList.countLabel = (execCount.textContent || '').trim();

        const approvalsTab = document.getElementById('approvals');
        if (approvalsTab) {
            const activeFilterBtn = approvalsTab.querySelector('.btn-group .btn.active');
            if (activeFilterBtn) {
                data.approvals.filter = (activeFilterBtn.dataset && activeFilterBtn.dataset.filter) ||
                    (activeFilterBtn.textContent || '').trim().toLowerCase();
            }
        }

        const dateRangeEl = document.getElementById('analytics-date-range');
        if (dateRangeEl) data.analytics.dateRange = dateRangeEl.value || '';
        const wfSelect = document.getElementById('analytics-workflow-select');
        if (wfSelect) {
            const sel = wfSelect.options[wfSelect.selectedIndex];
            data.analytics.workflow = sel ? (sel.textContent || '').trim() : '';
        }
        const customRange = document.getElementById('custom-date-range');
        if (customRange) {
            data.analytics.customRangeVisible = customRange.style.display !== 'none';
        }

        const docPage = document.getElementById('docPage');
        if (docPage) data.ui.theme = docPage.classList.contains('light-mode') ? 'light' : 'dark';

        if (data.activeTab.indexOf('Dashboard') === 0) {
            data.availableActions.push('Click "Refresh" to re-load executions and approvals');
            data.availableActions.push('Switch to "Executions" tab for the full execution history');
        } else if (data.activeTab.indexOf('Executions') === 0) {
            data.availableActions.push('Filter by status using the dropdown in the card header');
            data.availableActions.push('Click an execution row to drill into its details');
        } else if (data.activeTab.indexOf('Approvals') === 0) {
            if (data.stats.pendingApprovals > 0) {
                data.availableActions.push('Review the ' + data.stats.pendingApprovals + ' pending approval(s)');
            }
            data.availableActions.push('Switch the filter (Pending / Approved / Rejected / All) to see history');
        } else if (data.activeTab.indexOf('Analytics') === 0) {
            data.availableActions.push('Pick a time range and workflow, then click "Apply Filters"');
        } else if (data.activeTab.indexOf('Schedules') === 0) {
            data.availableActions.push('Edit or disable a schedule from its row');
        } else if (data.activeTab.indexOf('Logs') === 0) {
            data.availableActions.push('Look for errors and stack traces here when debugging a failed run');
        }

        return data;
    }
};

console.log('Workflow Monitor assistant context loaded');
