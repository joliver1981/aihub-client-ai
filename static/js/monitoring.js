// monitoring.js
let approvalModal = null;
let executionDetailModal = null;
let refreshInterval = null;

// Approvals Tab Functionality
let currentFilter = 'pending';
let currentPage = 1;
const itemsPerPage = 10;
let approvalsList = [];

// Global chart instances
let executionTrendChart = null;
let statusDistributionChart = null;
let topWorkflowsChart = null;
let durationChart = null;
let performanceData = [];

// Global variables for logs management
let logsData = [];
let logsCurrentPage = 1;
let logsPageSize = 50;
let logsTotalPages = 1;
let logsFilter = {
    execution_id: '',
    level: '',
    search: '',
    dateFrom: '',
    dateTo: ''
};

const TimezoneUtils = {
    getUserTimezone: function() {
        return Intl.DateTimeFormat().resolvedOptions().timeZone;
    },
    
    utcToLocal: function(utcDateString) {
        if (!utcDateString) return null;
        return moment.utc(utcDateString).local();
    },
    
    formatDate: function(utcDateString, format = 'MMM DD, YYYY h:mm A') {
        const localMoment = this.utcToLocal(utcDateString);
        if (!localMoment) return 'N/A';
        return localMoment.format(format);
    },

    // Format date for display - short version
    formatDateShort: function(utcDateString) {
        return this.formatDate(utcDateString, 'MMM DD h:mm A');
    },
    
    // Get relative time (e.g., "2 hours ago")
    getRelativeTime: function(utcDateString) {
        const localMoment = this.utcToLocal(utcDateString);
        if (!localMoment) return 'N/A';
        return localMoment.fromNow();
    },
    
    calculateDuration: function(startUtc, endUtc) {
        if (!startUtc) return null;
        const startMoment = moment.utc(startUtc);
        const endMoment = endUtc ? moment.utc(endUtc) : moment();
        return endMoment.diff(startMoment, 'seconds');
    },
    
    formatDuration: function(seconds) {
        if (!seconds) return 'N/A';
        const duration = moment.duration(seconds, 'seconds');
        const hours = Math.floor(duration.asHours());
        const mins = duration.minutes();
        const secs = duration.seconds();
        
        if (hours > 0) return `${hours}h ${mins}m`;
        if (mins > 0) return `${mins}m ${secs}s`;
        return `${secs}s`;
    }
};

document.addEventListener('DOMContentLoaded', function() {
    // Initialize modals
    approvalModal = new bootstrap.Modal(document.getElementById('approvalModal'));
    executionDetailModal = new bootstrap.Modal(document.getElementById('executionDetailModal'));
    
    // Initial data load
    loadDashboardData();
    
    // Set up automatic refresh (every 30 seconds)
    refreshInterval = setInterval(loadDashboardData, 30000);
    
    // Set up tab switching to load data
    document.querySelectorAll('.nav-link').forEach(tab => {
        tab.addEventListener('shown.bs.tab', e => {
            const targetId = e.target.getAttribute('href').substring(1);
            switch(targetId) {
                case 'dashboard':
                    loadDashboardData();
                    break;
                case 'executions':
                    loadExecutionsData();
                    break;
                case 'approvals':
                    loadApprovalsData();
                    break;
                case 'analytics':
                    loadAnalyticsData();
                    break;
                case 'logs':
                    fetchWorkflowLogs();
                    break;
            }
        });
    });

     // Initialize executions tab
     const statusFilter = document.getElementById('status-filter');
     if (statusFilter) {
         statusFilter.addEventListener('change', function() {
             executionsFilter.status = this.value;
             executionsPage = 1; // Reset to first page
             loadExecutionsData();
         });
     }
     
     const refreshBtn = document.getElementById('refresh-executions-btn');
     if (refreshBtn) {
         refreshBtn.addEventListener('click', function() {
             loadExecutionsData();
         });
     }
 
     // Load data when executions tab is shown
     document.querySelector('a[href="#executions"]').addEventListener('shown.bs.tab', function() {
         loadExecutionsData();
     });

         // Initialize approvals tab when it's shown
    document.querySelector('a[href="#approvals"]').addEventListener('shown.bs.tab', function(e) {
        loadApprovalsData();
    });

    // Refresh button handler
    document.getElementById('refresh-approvals-btn').addEventListener('click', function() {
        loadApprovalsData();
    });

    // Filter buttons
    document.querySelectorAll('.filters .btn-group button').forEach(button => {
        button.addEventListener('click', function() {
            // Update active button
            document.querySelectorAll('.filters .btn-group button').forEach(btn => {
                btn.classList.remove('active');
            });
            this.classList.add('active');
            
            // Set filter and reload
            currentFilter = this.getAttribute('data-filter');
            currentPage = 1;
            loadApprovalsData();
        });
    });

    // Initialize analytics tab when it's shown
    document.querySelector('a[href="#analytics"]').addEventListener('shown.bs.tab', function (e) {
        console.log('Analytics tab shown, initializing...');
        if (typeof initializeAnalytics === 'function') {
            initializeAnalytics();
        } else {
            console.error('Analytics initialization function not found');
        }
    });

        // Find all nav links and bind to their shown event
        document.querySelectorAll('.nav-link').forEach(tab => {
            tab.addEventListener('shown.bs.tab', e => {
                const targetId = e.target.getAttribute('href').substring(1);
                if (targetId === 'logs') {
                    // Initialize logs tab when it's activated
                    initLogsTab();
                }
            });
        });




// Initialize workflow schedules tab
const schedulesTab = document.querySelector('a[href="#schedules"]');
if (schedulesTab) {
    schedulesTab.addEventListener('shown.bs.tab', function() {
        loadWorkflowSchedules();
    });
}

// Add schedule button click handler
const addScheduleBtn = document.getElementById('addWorkflowScheduleBtn');
if (addScheduleBtn) {
    addScheduleBtn.addEventListener('click', function() {
        // Reset form
        document.getElementById('workflowScheduleForm').reset();
        
        // Load workflows for dropdown
        loadWorkflowsForDropdown();
        
        // Reset schedule type
        const scheduleType = document.getElementById('scheduleType');
        if (scheduleType) {
            scheduleType.value = '';
            toggleScheduleTypeFields();
        }
        
        // Show modal
        const modal = new bootstrap.Modal(document.getElementById('addWorkflowScheduleModal'));
        modal.show();
    });
}

// Schedule type change event
const scheduleType = document.getElementById('scheduleType');
if (scheduleType) {
    scheduleType.addEventListener('change', toggleScheduleTypeFields);
}

// Edit schedule type change event
const editScheduleType = document.getElementById('editScheduleType');
if (editScheduleType) {
    editScheduleType.addEventListener('change', toggleEditScheduleTypeFields);
}

// Save schedule button
const saveScheduleBtn = document.getElementById('saveWorkflowScheduleBtn');
if (saveScheduleBtn) {
    saveScheduleBtn.addEventListener('click', saveWorkflowSchedule);
}

// Update schedule button
const updateScheduleBtn = document.getElementById('updateWorkflowScheduleBtn');
if (updateScheduleBtn) {
    updateScheduleBtn.addEventListener('click', updateWorkflowSchedule);
}



});

function loadDashboardData() {
    // Load dashboard counts
    fetch('/api/workflow/stats/counts')
        .then(response => response.json())
        .then(data => {
            document.getElementById('active-workflows-count').textContent = data.active || 0;
            document.getElementById('paused-workflows-count').textContent = data.paused || 0;
            document.getElementById('completed-workflows-count').textContent = data.completed_today || 0;
            document.getElementById('failed-workflows-count').textContent = data.failed_today || 0;
            
            // Update approval count badge
            const approvalCount = data.pending_approvals || 0;
            const badge = document.getElementById('approval-count');
            badge.textContent = approvalCount;
            badge.style.display = approvalCount > 0 ? 'inline' : 'none';
        })
        .catch(error => console.error('Error loading dashboard counts:', error));
    
    // Load recent executions
    fetch('/api/workflow/executions?limit=10')
        .then(response => response.json())
        .then(data => {
            const tableBody = document.getElementById('recent-executions-table');
            tableBody.innerHTML = '';
            
            if (data.executions && data.executions.length > 0) {
                data.executions.forEach(execution => {
                    const row = document.createElement('tr');
                    
                    // Create status badge with appropriate color
                    let statusClass = 'secondary';
                    switch(execution.status.toLowerCase()) {
                        case 'running': statusClass = 'primary'; break;
                        case 'paused': statusClass = 'warning'; break;
                        case 'completed': statusClass = 'success'; break;
                        case 'failed': statusClass = 'danger'; break;
                        case 'cancelled': statusClass = 'dark'; break;
                    }
                    
                    // Calculate duration
                    const startDate = TimezoneUtils.formatDate(execution.started_at);
                    const endDate = execution.completed_at ? TimezoneUtils.formatDate(execution.completed_at) : new Date();
                    const durationMs = TimezoneUtils.calculateDuration(execution.started_at, execution.completed_at);
                    const duration = TimezoneUtils.formatDuration(durationMs);
                    
                    row.innerHTML = `
                        <td>${execution.execution_id.substring(0, 8)}...</td>
                        <td>${execution.workflow_name}</td>
                        <td><span class="badge bg-${statusClass}">${execution.status}</span></td>
                        <td>${TimezoneUtils.formatDate(execution.started_at)}</td>
                        <td>${duration}</td>
                        <td>
                            <button class="btn btn-sm btn-outline-primary" onclick="viewExecutionDetails('${execution.execution_id}')">
                                <i class="bi bi-eye"></i>
                            </button>
                        </td>
                    `;
                    
                    tableBody.appendChild(row);
                });
            } else {
                const row = document.createElement('tr');
                row.innerHTML = '<td colspan="6" class="text-center">No executions found</td>';
                tableBody.appendChild(row);
            }
        })
        .catch(error => console.error('Error loading recent executions:', error));
    
    // Load pending approvals
    fetch('/api/workflow/approvals?status=pending')
        .then(response => response.json())
        .then(data => {
            const tableBody = document.getElementById('pending-approvals-table');
            tableBody.innerHTML = '';
            
            if (data.approvals && data.approvals.length > 0) {
                data.approvals.forEach(approval => {
                    const row = document.createElement('tr');
                    
                    row.innerHTML = `
                        <td>${approval.workflow_name}</td>
                        <td>${approval.title}</td>
                        <td>${formatDate(approval.requested_at)}</td>
                        <td>${approval.assigned_to || 'Anyone'}</td>
                        <td>
                            <button class="btn btn-sm btn-primary" onclick="viewApprovalDetails('${approval.request_id}')">
                                <i class="bi bi-check2-square"></i> Review
                            </button>
                        </td>
                    `;
                    
                    tableBody.appendChild(row);
                });
            } else {
                const row = document.createElement('tr');
                row.innerHTML = '<td colspan="5" class="text-center">No pending approvals</td>';
                tableBody.appendChild(row);
            }
        })
        .catch(error => console.error('Error loading pending approvals:', error));
}

function viewApprovalDetails(requestId) {
    // Load approval details
    fetch(`/api/workflow/approvals/${requestId}`)
        .then(response => response.json())
        .then(data => {
            // Populate modal fields
            document.getElementById('approval-title').textContent = data.title;
            document.getElementById('approval-workflow-name').textContent = data.workflow_name;
            document.getElementById('approval-execution-id').textContent = data.execution_id;
            document.getElementById('approval-started-at').textContent = TimezoneUtils.formatDate(data.execution_started_at);
            document.getElementById('approval-step-name').textContent = data.node_name;
            document.getElementById('approval-description').textContent = data.description;
            
            // Format and display approval data JSON
            try {
                const jsonData = JSON.parse(data.approval_data);
                document.getElementById('approval-data').textContent = JSON.stringify(jsonData, null, 2);
            } catch(e) {
                document.getElementById('approval-data').textContent = data.approval_data || 'No data provided';
            }
            
            // Clear previous comments
            document.getElementById('approval-comments').value = '';
            
            // Set request ID for submission
            document.getElementById('approval-request-id').value = requestId;
            
            // Show the modal
            approvalModal.show();
        })
        .catch(error => {
            console.error('Error loading approval details:', error);
            alert('Error loading approval details. Please try again.');
        });
}

function respondToApproval(decision) {
    const requestId = document.getElementById('approval-request-id').value;
    const comments = document.getElementById('approval-comments').value;
    
    fetch(`/api/workflow/approvals/${requestId}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            status: decision,
            comments: comments
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            // Hide modal
            approvalModal.hide();
            
            // Show success message
            //alert(`Approval request ${decision} successfully.`);
            
            // Refresh data
            loadDashboardData();
        } else {
            alert(`Error: ${data.message}`);
        }
    })
    .catch(error => {
        console.error('Error submitting approval response:', error);
        alert('Error submitting your response. Please try again.');
    });
}

function viewExecutionDetails(executionId) {
    // Load execution details
    fetch(`/api/workflow/executions/${executionId}`)
        .then(response => response.json())
        .then(data => {
            // Populate header info
            document.getElementById('detail-workflow-name').textContent = data.workflow_name;
            document.getElementById('detail-execution-id').textContent = data.execution_id;
            document.getElementById('detail-status').textContent = data.status;
            document.getElementById('detail-started-at').textContent = TimezoneUtils.formatDate(data.started_at);
            document.getElementById('detail-completed-at').textContent = data.completed_at ? TimezoneUtils.formatDate(data.completed_at) : '-';
            document.getElementById('detail-initiated-by').textContent = data.initiated_by || 'System';
            
            // Populate current state info
            if (data.current_step) {
                document.getElementById('detail-active-node').textContent = data.current_step.node_name;
                document.getElementById('detail-step-status').textContent = data.current_step.status;
                
                if (data.current_step.status === 'Paused' && data.current_step.waiting_for_approval) {
                    document.getElementById('detail-waiting-for').textContent = 'Human Approval';
                } else if (data.current_step.status === 'Paused') {
                    document.getElementById('detail-waiting-for').textContent = 'Manual Resume';
                } else {
                    document.getElementById('detail-waiting-for').textContent = '-';
                }
            } else {
                document.getElementById('detail-active-node').textContent = '-';
                document.getElementById('detail-step-status').textContent = '-';
                document.getElementById('detail-waiting-for').textContent = '-';
            }
            
            // Control button visibility based on status
            const pauseBtn = document.getElementById('detail-pause-btn');
            const resumeBtn = document.getElementById('detail-resume-btn');
            const cancelBtn = document.getElementById('detail-cancel-btn');
            
            if (data.status === 'Running') {
                pauseBtn.style.display = 'inline-block';
                resumeBtn.style.display = 'none';
                cancelBtn.style.display = 'inline-block';
            } else if (data.status === 'Paused') {
                pauseBtn.style.display = 'none';
                resumeBtn.style.display = 'inline-block';
                cancelBtn.style.display = 'inline-block';
            } else {
                pauseBtn.style.display = 'none';
                resumeBtn.style.display = 'none';
                cancelBtn.style.display = 'none';
            }
            
            // Store execution ID for action buttons
            pauseBtn.setAttribute('data-execution-id', data.execution_id);
            resumeBtn.setAttribute('data-execution-id', data.execution_id);
            cancelBtn.setAttribute('data-execution-id', data.execution_id);
            
            // Load steps data
            loadExecutionSteps(data.execution_id);
            
            // Show the modal
            executionDetailModal.show();
        })
        .catch(error => {
            console.error('Error loading execution details:', error);
            alert('Error loading execution details. Please try again.');
        });
}

function loadExecutionSteps(executionId) {
    fetch(`/api/workflow/executions/${executionId}/steps`)
        .then(response => response.json())
        .then(data => {
            const tableBody = document.getElementById('execution-steps-table');
            tableBody.innerHTML = '';
            
            if (data.steps && data.steps.length > 0) {
                data.steps.forEach(step => {
                    const row = document.createElement('tr');
                    
                    // Create status badge with appropriate color
                    let statusClass = 'secondary';
                    switch(step.status.toLowerCase()) {
                        case 'running': statusClass = 'primary'; break;
                        case 'paused': statusClass = 'warning'; break;
                        case 'completed': statusClass = 'success'; break;
                        case 'failed': statusClass = 'danger'; break;
                        case 'pending': statusClass = 'secondary'; break;
                        case 'approved': statusClass = 'success'; break;
                        case 'rejected': statusClass = 'danger'; break;
                    }
                    
                    // Calculate duration
                    let duration = '-';
                    if (step.started_at) {
                        const startDate = TimezoneUtils.formatDate(step.started_at);
                        const endDate = step.completed_at ? TimezoneUtils.formatDate(step.completed_at) : new Date();
                        const durationMs = TimezoneUtils.calculateDuration(step.started_at, step.completed_at);
                        duration = TimezoneUtils.formatDuration(durationMs);
                    }
                    
                    row.innerHTML = `
                        <td>${step.node_name}</td>
                        <td>${step.node_type}</td>
                        <td><span class="badge bg-${statusClass}">${step.status}</span></td>
                        <td>${step.started_at ? TimezoneUtils.formatDate(step.started_at) : '-'}</td>
                        <td>${step.completed_at ? TimezoneUtils.formatDate(step.completed_at) : '-'}</td>
                        <td>${duration}</td>
                        <td>
                            <button class="btn btn-sm btn-outline-info" onclick="viewStepDetails('${step.step_execution_id}')">
                                <i class="bi bi-info-circle"></i>
                            </button>
                        </td>
                    `;
                    
                    tableBody.appendChild(row);
                });
            } else {
                const row = document.createElement('tr');
                row.innerHTML = '<td colspan="7" class="text-center">No step data available</td>';
                tableBody.appendChild(row);
            }
            
            // Also load variables and logs
            loadExecutionVariables(executionId);
            loadExecutionLogs(executionId);
        })
        .catch(error => console.error('Error loading execution steps:', error));
}

function loadExecutionVariables(executionId) {
    fetch(`/api/workflow/executions/${executionId}/variables`)
        .then(response => response.json())
        .then(data => {
            const tableBody = document.getElementById('execution-variables-table');
            tableBody.innerHTML = '';
            
            if (data.variables && Object.keys(data.variables).length > 0) {
                Object.entries(data.variables).forEach(([name, varData]) => {
                    const row = document.createElement('tr');
                    
                    // Format value based on type
                    let formattedValue = '';
                    if (typeof varData.value === 'object') {
                        formattedValue = '<pre class="mb-0">' + JSON.stringify(varData.value, null, 2) + '</pre>';
                    } else {
                        formattedValue = String(varData.value);
                    }
                    
                    row.innerHTML = `
                        <td>${name}</td>
                        <td>${varData.type}</td>
                        <td>${formattedValue}</td>
                        <td>${formatDate(varData.updated_at)}</td>
                    `;
                    
                    tableBody.appendChild(row);
                });
            } else {
                const row = document.createElement('tr');
                row.innerHTML = '<td colspan="4" class="text-center">No variables data available</td>';
                tableBody.appendChild(row);
            }
        })
        .catch(error => console.error('Error loading execution variables:', error));
}

function loadExecutionLogs(executionId) {
    const logLevel = document.getElementById('log-level-filter').value;
    const searchTerm = document.getElementById('log-search').value;
    
    let url = `/api/workflow/executions/${executionId}/logs`;
    if (logLevel || searchTerm) {
        url += '?';
        if (logLevel) url += `level=${logLevel}`;
        if (logLevel && searchTerm) url += '&';
        if (searchTerm) url += `search=${encodeURIComponent(searchTerm)}`;
    }
    
    fetch(url)
        .then(response => response.json())
        .then(data => {
            const logsContent = document.getElementById('execution-logs-content');
            
            if (data.logs && data.logs.length > 0) {
                let logText = '';
                
                data.logs.forEach(log => {
                    let levelClass = '';
                    switch(log.log_level.toLowerCase()) {
                        case 'info': levelClass = 'text-info'; break;
                        case 'warning': levelClass = 'text-warning'; break;
                        case 'error': levelClass = 'text-danger'; break;
                        case 'debug': levelClass = 'text-secondary'; break;
                    }
                    
                    const timestamp = TimezoneUtils.formatDate(log.timestamp);
                    logText += `<span class="text-muted">[${timestamp}]</span> <span class="${levelClass}">[${log.log_level.toUpperCase()}]</span> ${log.message}\n`;
                    
                    // If log has details, include them
                    if (log.details) {
                        try {
                            const details = JSON.parse(log.details);
                            logText += `<span class="text-muted ps-4">${JSON.stringify(details, null, 2)}</span>\n`;
                        } catch(e) {
                            // If not valid JSON, just display as is
                            logText += `<span class="text-muted ps-4">${log.details}</span>\n`;
                        }
                    }
                    
                    logText += '\n';
                });
                
                logsContent.innerHTML = logText;
            } else {
                logsContent.innerText = 'No logs available for this execution.';
            }
        })
        .catch(error => {
            console.error('Error loading execution logs:', error);
            document.getElementById('execution-logs-content').innerText = 'Error loading logs.';
        });
}

function refreshLogs() {
    // Get current execution ID from the modal
    const executionId = document.getElementById('detail-execution-id').textContent;
    if (executionId) {
        loadExecutionLogs(executionId);
    }
}

function pauseExecution() {
    const executionId = document.getElementById('detail-pause-btn').getAttribute('data-execution-id');
    
    fetch(`/api/workflow/executions/${executionId}/pause`, {
        method: 'POST'
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            //alert('Workflow execution paused successfully.');
            viewExecutionDetails(executionId); // Refresh details
        } else {
            alert(`Error: ${data.message}`);
        }
    })
    .catch(error => {
        console.error('Error pausing execution:', error);
        alert('Error pausing execution. Please try again.');
    });
}

function resumeExecution() {
    const executionId = document.getElementById('detail-resume-btn').getAttribute('data-execution-id');
    
    fetch(`/api/workflow/executions/${executionId}/resume`, {
        method: 'POST'
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            //alert('Workflow execution resumed successfully.');
            viewExecutionDetails(executionId); // Refresh details
        } else {
            alert(`Error: ${data.message}`);
        }
    })
    .catch(error => {
        console.error('Error resuming execution:', error);
        alert('Error resuming execution. Please try again.');
    });
}

function cancelExecution() {
    if (!confirm('Are you sure you want to cancel this workflow execution? This action cannot be undone.')) {
        return;
    }
    
    const executionId = document.getElementById('detail-cancel-btn').getAttribute('data-execution-id');
    
    fetch(`/api/workflow/executions/${executionId}/cancel`, {
        method: 'POST'
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            //alert('Workflow execution cancelled successfully.');
            executionDetailModal.hide();
            loadDashboardData(); // Refresh dashboard
        } else {
            alert(`Error: ${data.message}`);
        }
    })
    .catch(error => {
        console.error('Error cancelling execution:', error);
        alert('Error cancelling execution. Please try again.');
    });
}

function refreshDashboard() {
    loadDashboardData();
}

// Utility function to format dates
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleString();
}

// Utility function to format durations
function formatDuration(ms) {
    if (ms < 0) ms = 0;
    
    const seconds = Math.floor(ms / 1000);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    
    if (hours > 0) {
        return `${hours}h ${minutes % 60}m ${seconds % 60}s`;
    } else if (minutes > 0) {
        return `${minutes}m ${seconds % 60}s`;
    } else {
        return `${seconds}s`;
    }
}




// Executions tab functionality
let executionsPage = 1;
const executionsPerPage = 10;
let executionsFilter = {
    status: ''
};

function loadExecutionsData() {
    // Show loading indicator
    document.getElementById('executions-table-body').innerHTML = 
        '<tr><td colspan="7" class="text-center"><i class="bi bi-hourglass-split me-2"></i>Loading executions...</td></tr>';
    
    // Build query parameters
    let params = new URLSearchParams();
    params.append('limit', executionsPerPage);
    params.append('offset', (executionsPage - 1) * executionsPerPage);
    
    if (executionsFilter.status) {
        params.append('status', executionsFilter.status);
    }
    
    // Fetch executions from the API
    fetch('/api/workflow/executions?' + params.toString())
        .then(response => {
            if (!response.ok) {
                throw new Error('Failed to load executions');
            }
            return response.json();
        })
        .then(data => {
            updateExecutionsTable(data);
        })
        .catch(error => {
            console.error('Error loading executions:', error);
            document.getElementById('executions-table-body').innerHTML = 
                `<tr><td colspan="7" class="text-center text-danger">Error loading executions: ${error.message}</td></tr>`;
        });
}

function updateExecutionsTable(data) {
    const tableBody = document.getElementById('executions-table-body');
    const countElement = document.getElementById('executions-count');
    
    // Update count
    countElement.textContent = data.count || 0;
    
    // Clear table
    tableBody.innerHTML = '';
    
    if (!data.executions || data.executions.length === 0) {
        tableBody.innerHTML = '<tr><td colspan="7" class="text-center">No executions found</td></tr>';
        return;
    }
    
    // Add rows for each execution
    data.executions.forEach(execution => {
        const row = document.createElement('tr');
        
        // Create status badge with appropriate color
        let statusClass = 'secondary';
        switch(execution.status.toLowerCase()) {
            case 'running': statusClass = 'primary'; break;
            case 'paused': statusClass = 'warning'; break;
            case 'completed': statusClass = 'success'; break;
            case 'failed': statusClass = 'danger'; break;
            case 'cancelled': statusClass = 'dark'; break;
        }
        
        // Format dates
        //const startedAt = new Date(execution.started_at).toLocaleString();
        const startedAt = TimezoneUtils.formatDateShort(execution.started_at);

        //const completedAt = execution.completed_at ? new Date(execution.completed_at).toLocaleString() : '-';
        const completedAt = execution.completed_at ? TimezoneUtils.formatDateShort(execution.completed_at) : '-';
        
        // Create execution ID shorter display
        const shortId = execution.execution_id.substring(0, 8) + '...';
        
        row.innerHTML = `
            <td><span title="${execution.execution_id}">${shortId}</span></td>
            <td>${execution.workflow_name}</td>
            <td>${startedAt}</td>
            <td>${completedAt}</td>
            <td><span class="badge bg-${statusClass}">${execution.status}</span></td>
            <td>${execution.initiated_by || 'System'}</td>
            <td>
                <div class="btn-group btn-group-sm">
                    <button class="btn btn-sm btn-outline-primary" onclick="viewExecutionDetails('${execution.execution_id}')">
                        <i class="bi bi-eye"></i>
                    </button>
                    ${execution.status === 'Running' ? 
                        `<button class="btn btn-sm btn-outline-warning" onclick="pauseExecution('${execution.execution_id}')">
                            <i class="bi bi-pause-fill"></i>
                        </button>` : ''}
                    ${execution.status === 'Paused' ? 
                        `<button class="btn btn-sm btn-outline-primary" onclick="resumeExecution('${execution.execution_id}')">
                            <i class="bi bi-play-fill"></i>
                        </button>` : ''}
                    ${(execution.status === 'Running' || execution.status === 'Paused') ? 
                        `<button class="btn btn-sm btn-outline-danger" onclick="confirmCancelExecution('${execution.execution_id}')">
                            <i class="bi bi-x-circle"></i>
                        </button>` : ''}
                </div>
            </td>
        `;
        
        tableBody.appendChild(row);
    });
    
    // Update pagination (simplified version)
    updateExecutionsPagination(Math.ceil(data.count / executionsPerPage));
}

function updateExecutionsPagination(totalPages) {
    const paginationElement = document.getElementById('executions-pagination');
    paginationElement.innerHTML = '';
    
    if (totalPages <= 1) {
        return;
    }
    
    // Previous button
    const prevLi = document.createElement('li');
    prevLi.className = `page-item ${executionsPage === 1 ? 'disabled' : ''}`;
    prevLi.innerHTML = `
        <a class="page-link" href="#" aria-label="Previous" onclick="changeExecutionsPage(${executionsPage - 1}); return false;">
            <span aria-hidden="true">&laquo;</span>
        </a>
    `;
    paginationElement.appendChild(prevLi);
    
    // Page numbers
    const maxPages = 5; // Show at most 5 page numbers
    const startPage = Math.max(1, Math.min(executionsPage - Math.floor(maxPages / 2), totalPages - maxPages + 1));
    const endPage = Math.min(startPage + maxPages - 1, totalPages);
    
    for (let i = startPage; i <= endPage; i++) {
        const pageLi = document.createElement('li');
        pageLi.className = `page-item ${executionsPage === i ? 'active' : ''}`;
        pageLi.innerHTML = `
            <a class="page-link" href="#" onclick="changeExecutionsPage(${i}); return false;">${i}</a>
        `;
        paginationElement.appendChild(pageLi);
    }
    
    // Next button
    const nextLi = document.createElement('li');
    nextLi.className = `page-item ${executionsPage === totalPages ? 'disabled' : ''}`;
    nextLi.innerHTML = `
        <a class="page-link" href="#" aria-label="Next" onclick="changeExecutionsPage(${executionsPage + 1}); return false;">
            <span aria-hidden="true">&raquo;</span>
        </a>
    `;
    paginationElement.appendChild(nextLi);
}

function changeExecutionsPage(page) {
    executionsPage = page;
    loadExecutionsData();
}

function confirmCancelExecution(executionId) {
    if (confirm('Are you sure you want to cancel this workflow execution? This action cannot be undone.')) {
        cancelExecution(executionId);
    }
}

function cancelExecution(executionId) {
    fetch(`/api/workflow/executions/${executionId}/cancel`, {
        method: 'POST'
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            //alert('Workflow execution cancelled successfully.');
            loadExecutionsData(); // Refresh the table
        } else {
            alert(`Error: ${data.message}`);
        }
    })
    .catch(error => {
        console.error('Error cancelling execution:', error);
        alert('Error cancelling execution. Please try again.');
    });
}

function pauseExecution(executionId) {
    fetch(`/api/workflow/executions/${executionId}/pause`, {
        method: 'POST'
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            //alert('Workflow execution paused successfully.');
            loadExecutionsData(); // Refresh the table
        } else {
            alert(`Error: ${data.message}`);
        }
    })
    .catch(error => {
        console.error('Error pausing execution:', error);
        alert('Error pausing execution. Please try again.');
    });
}

function resumeExecution(executionId) {
    fetch(`/api/workflow/executions/${executionId}/resume`, {
        method: 'POST'
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            //alert('Workflow execution resumed successfully.');
            loadExecutionsData(); // Refresh the table
        } else {
            alert(`Error: ${data.message}`);
        }
    })
    .catch(error => {
        console.error('Error resuming execution:', error);
        alert('Error resuming execution. Please try again.');
    });
}

// Execution details functionality
function viewExecutionDetails(executionId) {
    // Fetch execution details
    fetch(`/api/workflow/executions/${executionId}`)
        .then(response => {
            if (!response.ok) {
                throw new Error(`Failed to load execution details: ${response.status} ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            // Populate header info
            document.getElementById('detail-workflow-name').textContent = data.workflow_name;
            document.getElementById('detail-execution-id').textContent = data.execution_id;
            document.getElementById('detail-status').textContent = data.status;
            document.getElementById('detail-started-at').textContent = TimezoneUtils.formatDate(data.started_at);
            document.getElementById('detail-completed-at').textContent = data.completed_at ? TimezoneUtils.formatDate(data.completed_at) : '-';
            document.getElementById('detail-initiated-by').textContent = data.initiated_by || 'System';
            
            // Populate current state info
            if (data.current_step) {
                document.getElementById('detail-active-node').textContent = data.current_step.node_name;
                document.getElementById('detail-step-status').textContent = data.current_step.status;
                
                if (data.current_step.status === 'Paused' && data.current_step.waiting_for_approval) {
                    document.getElementById('detail-waiting-for').textContent = 'Human Approval';
                } else if (data.current_step.status === 'Paused') {
                    document.getElementById('detail-waiting-for').textContent = 'Manual Resume';
                } else {
                    document.getElementById('detail-waiting-for').textContent = '-';
                }
            } else {
                document.getElementById('detail-active-node').textContent = '-';
                document.getElementById('detail-step-status').textContent = '-';
                document.getElementById('detail-waiting-for').textContent = '-';
            }
            
            // Control button visibility based on status
            const pauseBtn = document.getElementById('detail-pause-btn');
            const resumeBtn = document.getElementById('detail-resume-btn');
            const cancelBtn = document.getElementById('detail-cancel-btn');
            
            if (data.status === 'Running') {
                pauseBtn.style.display = 'inline-block';
                resumeBtn.style.display = 'none';
                cancelBtn.style.display = 'inline-block';
            } else if (data.status === 'Paused') {
                pauseBtn.style.display = 'none';
                resumeBtn.style.display = 'inline-block';
                cancelBtn.style.display = 'inline-block';
            } else {
                pauseBtn.style.display = 'none';
                resumeBtn.style.display = 'none';
                cancelBtn.style.display = 'none';
            }
            
            // Store execution ID for action buttons
            pauseBtn.setAttribute('data-execution-id', data.execution_id);
            resumeBtn.setAttribute('data-execution-id', data.execution_id);
            cancelBtn.setAttribute('data-execution-id', data.execution_id);
            
            // Load steps data
            loadExecutionSteps(data.execution_id);
            
            // Show the modal
            const modal = new bootstrap.Modal(document.getElementById('executionDetailModal'));
            modal.show();
        })
        .catch(error => {
            console.error('Error loading execution details:', error);
            alert('Error loading execution details. Please try again.');
        });
}

function loadExecutionSteps(executionId) {
    fetch(`/api/workflow/executions/${executionId}/steps`)
        .then(response => {
            if (!response.ok) {
                throw new Error('Failed to load execution steps');
            }
            return response.json();
        })
        .then(data => {
            const tableBody = document.getElementById('execution-steps-table');
            tableBody.innerHTML = '';
            
            if (data.steps && data.steps.length > 0) {
                data.steps.forEach(step => {
                    const row = document.createElement('tr');
                    
                    // Create status badge with appropriate color
                    let statusClass = 'secondary';
                    switch(step.status.toLowerCase()) {
                        case 'running': statusClass = 'primary'; break;
                        case 'paused': statusClass = 'warning'; break;
                        case 'completed': statusClass = 'success'; break;
                        case 'failed': statusClass = 'danger'; break;
                        case 'pending': statusClass = 'secondary'; break;
                        case 'approved': statusClass = 'success'; break;
                        case 'rejected': statusClass = 'danger'; break;
                    }
                    
                    // Calculate duration
                    let duration = '-';
                    if (step.started_at) {
                        const startDate = TimezoneUtils.formatDate(step.started_at);
                        const endDate = step.completed_at ? TimezoneUtils.formatDate(step.completed_at) : new Date();
                        const durationMs = TimezoneUtils.calculateDuration(step.started_at, step.completed_at);
                        duration = TimezoneUtils.formatDuration(durationMs);
                        //const durationMs = endDate - startDate;
                        //duration = formatDuration(durationMs);
                    }
                    
                    row.innerHTML = `
                        <td>${step.node_name}</td>
                        <td>${step.node_type}</td>
                        <td><span class="badge bg-${statusClass}">${step.status}</span></td>
                        <td>${step.started_at ? TimezoneUtils.formatDate(step.started_at) : '-'}</td>
                        <td>${step.completed_at ? TimezoneUtils.formatDate(step.completed_at) : '-'}</td>
                        <td>${duration}</td>
                        <td>
                            <button class="btn btn-sm btn-outline-info" onclick="viewStepDetails('${step.step_execution_id}')">
                                <i class="bi bi-info-circle"></i>
                            </button>
                        </td>
                    `;
                    
                    tableBody.appendChild(row);
                });
            } else {
                const row = document.createElement('tr');
                row.innerHTML = '<td colspan="7" class="text-center">No step data available</td>';
                tableBody.appendChild(row);
            }
            
            // Also load variables and logs
            loadExecutionVariables(executionId);
            loadExecutionLogs(executionId);
        })
        .catch(error => {
            console.error('Error loading execution steps:', error);
            const tableBody = document.getElementById('execution-steps-table');
            tableBody.innerHTML = `<tr><td colspan="7" class="text-center text-danger">Error loading steps: ${error.message}</td></tr>`;
        });
}



function loadExecutionVariables(executionId) {
    fetch(`/api/workflow/executions/${executionId}/variables`)
        .then(response => {
            if (!response.ok) {
                throw new Error('Failed to load execution variables');
            }
            return response.json();
        })
        .then(data => {
            const tableBody = document.getElementById('execution-variables-table');
            tableBody.innerHTML = '';
            
            if (data.variables && Object.keys(data.variables).length > 0) {
                Object.entries(data.variables).forEach(([name, varData]) => {
                    const row = document.createElement('tr');
                    
                    // Format value based on type
                    let formattedValue = '';
                    if (typeof varData.value === 'object') {
                        formattedValue = '<pre class="mb-0">' + JSON.stringify(varData.value, null, 2) + '</pre>';
                    } else {
                        formattedValue = String(varData.value);
                    }
                    
                    row.innerHTML = `
                        <td>${name}</td>
                        <td>${varData.type}</td>
                        <td>${formattedValue}</td>
                        <td>${formatDate(varData.updated_at)}</td>
                    `;
                    
                    tableBody.appendChild(row);
                });
            } else {
                const row = document.createElement('tr');
                row.innerHTML = '<td colspan="4" class="text-center">No variables data available</td>';
                tableBody.appendChild(row);
            }
        })
        .catch(error => {
            console.error('Error loading execution variables:', error);
            const tableBody = document.getElementById('execution-variables-table');
            tableBody.innerHTML = `<tr><td colspan="4" class="text-center text-danger">Error loading variables: ${error.message}</td></tr>`;
        });
}

function loadExecutionLogs(executionId) {
    const logLevel = document.getElementById('log-level-filter').value;
    const searchTerm = document.getElementById('log-search').value;
    
    let url = `/api/workflow/executions/${executionId}/logs`;
    if (logLevel || searchTerm) {
        url += '?';
        if (logLevel) url += `level=${logLevel}`;
        if (logLevel && searchTerm) url += '&';
        if (searchTerm) url += `search=${encodeURIComponent(searchTerm)}`;
    }
    
    fetch(url)
        .then(response => {
            if (!response.ok) {
                throw new Error('Failed to load execution logs');
            }
            return response.json();
        })
        .then(data => {
            const logsContent = document.getElementById('execution-logs-content');
            
            if (data.logs && data.logs.length > 0) {
                let logText = '';
                
                data.logs.forEach(log => {
                    let levelClass = '';
                    switch(log.log_level.toLowerCase()) {
                        case 'info': levelClass = 'text-info'; break;
                        case 'warning': levelClass = 'text-warning'; break;
                        case 'error': levelClass = 'text-danger'; break;
                        case 'debug': levelClass = 'text-secondary'; break;
                    }
                    
                    const timestamp = TimezoneUtils.formatDate(log.timestamp);
                    logText += `<span class="text-muted">[${timestamp}]</span> <span class="${levelClass}">[${log.log_level.toUpperCase()}]</span> ${log.message}\n`;
                    
                    // If log has details, include them
                    if (log.details) {
                        try {
                            const details = JSON.parse(log.details);
                            logText += `<span class="text-muted ps-4">${JSON.stringify(details, null, 2)}</span>\n`;
                        } catch(e) {
                            // If not valid JSON, just display as is
                            logText += `<span class="text-muted ps-4">${log.details}</span>\n`;
                        }
                    }
                    
                    logText += '\n';
                });
                
                logsContent.innerHTML = logText;
            } else {
                logsContent.innerText = 'No logs available for this execution.';
            }
        })
        .catch(error => {
            console.error('Error loading execution logs:', error);
            document.getElementById('execution-logs-content').innerText = `Error loading logs: ${error.message}`;
        });
}

function viewStepDetails(stepExecutionId) {
    // This function can be expanded to show more detailed step information
    alert('Step details functionality will be implemented in a future update.');
}

function refreshLogs() {
    // Get current execution ID from the modal
    const executionId = document.getElementById('detail-execution-id').textContent;
    if (executionId) {
        loadExecutionLogs(executionId);
    }
}


// Utility function to format dates
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleString();
}

// Utility function to format durations
function formatDuration(ms) {
    if (ms < 0) ms = 0;
    
    const seconds = Math.floor(ms / 1000);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    
    if (hours > 0) {
        return `${hours}h ${minutes % 60}m ${seconds % 60}s`;
    } else if (minutes > 0) {
        return `${minutes}m ${seconds % 60}s`;
    } else {
        return `${seconds}s`;
    }
}


// Function to view step details
function viewStepDetails(stepExecutionId) {
    fetch(`/api/workflow/steps/${stepExecutionId}`)
        .then(response => {
            if (!response.ok) {
                // If endpoint doesn't exist, use a simple alert for now
                if (response.status === 404) {
                    alert('Step details functionality will be implemented in a future update.');
                    return null;
                }
                throw new Error(`Failed to load step details: ${response.status} ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            if (!data) return; // Skip if no data (future implementation message shown)
            
            // Populate step details modal
            document.getElementById('step-detail-node-name').textContent = data.node_name;
            document.getElementById('step-detail-node-type').textContent = data.node_type;
            
            // Create status badge with appropriate color
            let statusClass = 'secondary';
            switch(data.status.toLowerCase()) {
                case 'running': statusClass = 'primary'; break;
                case 'paused': statusClass = 'warning'; break;
                case 'completed': statusClass = 'success'; break;
                case 'failed': statusClass = 'danger'; break;
                case 'pending': statusClass = 'secondary'; break;
                case 'approved': statusClass = 'success'; break;
                case 'rejected': statusClass = 'danger'; break;
            }
            
            document.getElementById('step-detail-status').innerHTML = 
                `<span class="badge bg-${statusClass}">${data.status}</span>`;
            
            document.getElementById('step-detail-started-at').textContent = 
                data.started_at ? TimezoneUtils.formatDate(data.started_at) : '-';
            document.getElementById('step-detail-completed-at').textContent = 
                data.completed_at ? TimezoneUtils.formatDate(data.completed_at) : '-';
            
            // Calculate duration
            let duration = '-';
            if (data.started_at) {
                const startDate = TimezoneUtils.formatDate(data.started_at);
                const endDate = data.completed_at ? TimezoneUtils.formatDate(data.completed_at) : new Date();
                const durationMs = TimezoneUtils.calculateDuration(data.started_at, data.completed_at);
                duration = TimezoneUtils.formatDuration(durationMs);
                //const durationMs = endDate - startDate;
                //duration = formatDuration(durationMs);
            }
            document.getElementById('step-detail-duration').textContent = duration;
            
            // Format input data as JSON
            if (data.input_data) {
                try {
                    const inputData = typeof data.input_data === 'object' ? 
                        data.input_data : JSON.parse(data.input_data);
                    document.getElementById('step-detail-input').textContent = 
                        JSON.stringify(inputData, null, 2);
                } catch (e) {
                    document.getElementById('step-detail-input').textContent = data.input_data;
                }
            } else {
                document.getElementById('step-detail-input').textContent = 'No input data available';
            }
            
            // Format output data as JSON
            if (data.output_data) {
                try {
                    const outputData = typeof data.output_data === 'object' ? 
                        data.output_data : JSON.parse(data.output_data);
                    document.getElementById('step-detail-output').textContent = 
                        JSON.stringify(outputData, null, 2);
                } catch (e) {
                    document.getElementById('step-detail-output').textContent = data.output_data;
                }
            } else {
                document.getElementById('step-detail-output').textContent = 'No output data available';
            }
            
            // Show error message if any
            if (data.error_message) {
                document.getElementById('step-detail-error').textContent = data.error_message;
                document.getElementById('step-detail-error').parentElement.style.display = 'block';
            } else {
                document.getElementById('step-detail-error').textContent = 'No errors';
                document.getElementById('step-detail-error').parentElement.style.display = 
                    data.status.toLowerCase() === 'failed' ? 'block' : 'none';
            }
            
            // Show the modal
            const modal = new bootstrap.Modal(document.getElementById('stepDetailModal'));
            modal.show();
        })
        .catch(error => {
            console.error('Error loading step details:', error);
            alert(`Error loading step details: ${error.message}`);
        });
}


// Load approvals from the server
function loadApprovalsData() {
    // Show loading state
    document.getElementById('approvals-table').innerHTML = `
        <tr>
            <td colspan="6" class="text-center">
                <div class="spinner-border spinner-border-sm text-primary" role="status">
                    <span class="sr-only">Loading...</span>
                </div>
                Loading approvals...
            </td>
        </tr>
    `;

    // Fetch approvals from API
    let url = '/api/workflow/approvals';
    if (currentFilter !== 'all') {
        url += `?status=${currentFilter}`;
    }

    fetch(url)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            approvalsList = data.approvals || [];
            displayApprovals();
        })
        .catch(error => {
            console.error('Error loading approvals:', error);
            document.getElementById('approvals-table').innerHTML = `
                <tr>
                    <td colspan="6" class="text-center text-danger">
                        Error loading approvals: ${error.message}
                    </td>
                </tr>
            `;
        });
}

// Display the list of approvals with pagination
function displayApprovals() {
    const tableBody = document.getElementById('approvals-table');
    
    // Calculate pagination
    const totalPages = Math.ceil(approvalsList.length / itemsPerPage);
    const startIdx = (currentPage - 1) * itemsPerPage;
    const endIdx = Math.min(startIdx + itemsPerPage, approvalsList.length);
    const currentApprovals = approvalsList.slice(startIdx, endIdx);
    
    // Clear the table
    tableBody.innerHTML = '';
    
    // If no approvals found
    if (currentApprovals.length === 0) {
        tableBody.innerHTML = `
            <tr>
                <td colspan="6" class="text-center">
                    No ${currentFilter !== 'all' ? currentFilter : ''} approvals found
                </td>
            </tr>
        `;
        document.getElementById('approvals-pagination').innerHTML = '';
        return;
    }
    
    // Add each approval to the table
    currentApprovals.forEach(approval => {
        // Format date
        const requestedAt =TimezoneUtils.formatDate(approval.requested_at);
        const formattedDate = requestedAt;
        
        // Create the status badge
        let statusClass = 'secondary';
        if (approval.status === 'Pending') statusClass = 'warning';
        if (approval.status === 'Approved') statusClass = 'success';
        if (approval.status === 'Rejected') statusClass = 'danger';
        
        // Create action button based on status
        let actionButton = '';
        if (approval.status === 'Pending') {
            actionButton = `
                <button class="btn btn-sm btn-primary" onclick="viewApprovalDetails('${approval.request_id}')">
                    <i class="bi bi-check2-square"></i> Review
                </button>
            `;
        } else {
            actionButton = `
                <button class="btn btn-sm btn-outline-secondary" onclick="viewApprovalDetails('${approval.request_id}')">
                    <i class="bi bi-eye"></i> View
                </button>
            `;
        }
        
        // Create the row
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${approval.workflow_name}</td>
            <td>${approval.title}</td>
            <td>${formattedDate}</td>
            <td><span class="badge bg-${statusClass}">${approval.status}</span></td>
            <td>${approval.assigned_to || 'Anyone'}</td>
            <td>
                ${actionButton}
            </td>
        `;
        
        tableBody.appendChild(row);
    });
    
    // Update pagination
    updatePagination(totalPages);
}

// Update pagination controls
function updatePagination(totalPages) {
    const pagination = document.getElementById('approvals-pagination');
    
    if (totalPages <= 1) {
        pagination.innerHTML = '';
        return;
    }
    
    let paginationHtml = '';
    
    // Previous button
    paginationHtml += `
        <li class="page-item ${currentPage === 1 ? 'disabled' : ''}">
            <a class="page-link" href="#" data-page="${currentPage - 1}" aria-label="Previous">
                <span aria-hidden="true">&laquo;</span>
            </a>
        </li>
    `;
    
    // Page numbers
    for (let i = 1; i <= totalPages; i++) {
        paginationHtml += `
            <li class="page-item ${i === currentPage ? 'active' : ''}">
                <a class="page-link" href="#" data-page="${i}">${i}</a>
            </li>
        `;
    }
    
    // Next button
    paginationHtml += `
        <li class="page-item ${currentPage === totalPages ? 'disabled' : ''}">
            <a class="page-link" href="#" data-page="${currentPage + 1}" aria-label="Next">
                <span aria-hidden="true">&raquo;</span>
            </a>
        </li>
    `;
    
    pagination.innerHTML = paginationHtml;
    
    // Add click handlers to pagination links
    document.querySelectorAll('#approvals-pagination .page-link').forEach(link => {
        link.addEventListener('click', function(e) {
            e.preventDefault();
            const page = parseInt(this.getAttribute('data-page'));
            if (page > 0 && page <= totalPages) {
                currentPage = page;
                displayApprovals();
            }
        });
    });
}

// View approval details and allow approve/reject
function viewApprovalDetails(requestId) {
    // Fetch details for this approval
    fetch(`/api/workflow/approvals/${requestId}`)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            // Format dates
            const requestedAt =TimezoneUtils.formatDate(data.requested_at);
            const formattedRequestDate = requestedAt;
            
            const startedAt =TimezoneUtils.formatDate(data.execution_started_at);
            const formattedStartDate = startedAt;
            
            // Parse approval data if available
            let approvalDataHtml = '';
            try {
                const approvalData = data.approval_data ? JSON.parse(data.approval_data) : {};
                approvalDataHtml = `<pre id="approval-data" class="bg-light p-3 rounded">${JSON.stringify(approvalData, null, 2)}</pre>`;
            } catch (e) {
                approvalDataHtml = `<pre id="approval-data" class="bg-light p-3 rounded">${data.approval_data || 'No data provided'}</pre>`;
            }
            
            // Determine if read-only (already processed)
            const isReadOnly = data.status !== 'Pending';
            
            // Set up modal content
            document.getElementById('approval-title').textContent = data.title;
            document.getElementById('approval-workflow-name').textContent = data.workflow_name;
            document.getElementById('approval-execution-id').textContent = data.execution_id;
            document.getElementById('approval-started-at').textContent = formattedStartDate;
            document.getElementById('approval-step-name').textContent = data.node_name;
            document.getElementById('approval-description').textContent = data.description || 'No description provided';
            
            // Set approval data
            document.getElementById('approval-data').outerHTML = approvalDataHtml;
            
            // Set request ID for form submission
            document.getElementById('approval-request-id').value = requestId;
            
            // Add response info if already processed
            if (isReadOnly) {
                // Format response date if available
                let responseInfo = '';
                if (data.response_at) {
                    const responseDate =TimezoneUtils.formatDate(data.response_at);
                    const formattedResponseDate = responseDate;
                    responseInfo = `
                        <div class="alert ${data.status === 'Approved' ? 'alert-success' : 'alert-danger'}">
                            <strong>${data.status}</strong> by ${data.responded_by || 'System'} on ${formattedResponseDate}
                            ${data.comments ? `<p class="mt-2 mb-0"><strong>Comments:</strong> ${data.comments}</p>` : ''}
                        </div>
                    `;
                }
                
                // Add response info to modal
                document.querySelector('.approval-response').innerHTML = `
                    <h6>Response</h6>
                    ${responseInfo}
                `;
                
                // Hide action buttons
                document.getElementById('approve-btn').style.display = 'none';
                document.getElementById('reject-btn').style.display = 'none';
            } else {
                // Reset comments field
                document.getElementById('approval-comments').value = '';
                
                // Show approval/reject buttons
                document.getElementById('approve-btn').style.display = 'inline-block';
                document.getElementById('reject-btn').style.display = 'inline-block';
                
                // Reset response area
                document.querySelector('.approval-response').innerHTML = `
                    <h6>Your Response</h6>
                    <div class="mb-3">
                        <label for="approval-comments" class="form-label">Comments</label>
                        <textarea id="approval-comments" class="form-control" rows="3" placeholder="Add optional comments about your decision"></textarea>
                    </div>
                `;
            }
            
            // Show the modal
            approvalModal.show();
        })
        .catch(error => {
            console.error('Error loading approval details:', error);
            alert('Error loading approval details. Please try again.');
        });
}

// Handle approve/reject actions
function respondToApproval(decision) {
    const requestId = document.getElementById('approval-request-id').value;
    const comments = document.getElementById('approval-comments').value;
    
    fetch(`/api/workflow/approvals/${requestId}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            status: decision,
            comments: comments
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            // Hide modal
            approvalModal.hide();
            
            // Show success message
            //alert(`Approval request ${decision} successfully.`);
            
            // Refresh approvals list
            loadApprovalsData();
        } else {
            alert(`Error: ${data.message}`);
        }
    })
    .catch(error => {
        console.error('Error submitting approval response:', error);
        alert('Error submitting your response. Please try again.');
    });
}


// Initialize analytics tab
function initializeAnalytics() {
    console.log('Initializing analytics tab...');
    
    // Set up date range selector behavior
    document.getElementById('analytics-date-range').addEventListener('change', function() {
        const customDateRange = document.getElementById('custom-date-range');
        if (this.value === 'custom') {
            customDateRange.style.display = 'block';
        } else {
            customDateRange.style.display = 'none';
        }
    });
    
    // Set default dates for custom range
    const today = new Date();
    const thirtyDaysAgo = new Date();
    thirtyDaysAgo.setDate(today.getDate() - 30);
    
    document.getElementById('analytics-end-date').valueAsDate = today;
    document.getElementById('analytics-start-date').valueAsDate = thirtyDaysAgo;
    
    // Load workflows for filter dropdown
    loadWorkflowsForAnalytics();
    
    // Apply filters button click event
    document.getElementById('apply-analytics-filters').addEventListener('click', loadAnalyticsData);
    
    // Initial data load
    loadAnalyticsData();
}

// Load workflows for the analytics filter dropdown
function loadWorkflowsForAnalytics() {
    fetch('/get/workflows')
        .then(response => response.json())
        .then(data => {
            // Parse the workflows data - handles different response formats
            let workflows = [];
            if (typeof data === 'string') {
                workflows = JSON.parse(data);
            } else if (Array.isArray(data)) {
                workflows = data;
            } else if (typeof data === 'object') {
                workflows = Object.values(data);
            }
            
            const workflowSelect = document.getElementById('analytics-workflow-select');
            // Keep the "All Workflows" option and add the rest
            workflowSelect.innerHTML = '<option value="all">All Workflows</option>';
            
            workflows.forEach(workflow => {
                const workflowName = workflow.workflow_name || workflow.name || 'Unnamed';
                const workflowId = workflow.id || workflow.ID;
                const option = document.createElement('option');
                option.value = workflowId;
                option.textContent = workflowName;
                workflowSelect.appendChild(option);
            });
        })
        .catch(error => {
            console.error('Error loading workflows for analytics:', error);
        });
}

// Fetch analytics data based on selected filters
function loadAnalyticsData() {
    // Show loading indicators
    showLoadingState();
    
    // Get filter values
    const dateRangeSelect = document.getElementById('analytics-date-range');
    const workflowSelect = document.getElementById('analytics-workflow-select');
    
    let startDate, endDate;
    const today = new Date();
    
    if (dateRangeSelect.value === 'custom') {
        startDate = document.getElementById('analytics-start-date').valueAsDate;
        endDate = document.getElementById('analytics-end-date').valueAsDate;
    } else {
        const daysAgo = parseInt(dateRangeSelect.value);
        startDate = new Date();
        startDate.setDate(today.getDate() - daysAgo);
        endDate = today;
    }
    
    const workflowId = workflowSelect.value;
    
    // Format dates for API
    const formattedStartDate = formatDate2(startDate);
    const formattedEndDate = formatDate2(endDate);
    
    // Build API URL
    let apiUrl = `/api/workflow/analytics?start_date=${formattedStartDate}&end_date=${formattedEndDate}`;
    if (workflowId !== 'all') {
        apiUrl += `&workflow_id=${workflowId}`;
    }
    
    // Fetch data from the server
    fetch(apiUrl)
        .then(response => {
            if (!response.ok) {
                // If API route is not implemented, fallback to mock data for demonstration
                console.warn('Analytics API not found.');
                return Promise.resolve({ mockData: false });
            }
            return response.json();
        })
        .then(data => {
            // If we received mock data or the API returned an error, generate mock data
            if (data.mockData || data.status === 'error') {
                return generateMockAnalyticsData(startDate, endDate, workflowId);
            }
            return data;
        })
        .then(analyticsData => {
            // Process and display the data
            updateAnalyticsDashboard(analyticsData);
        })
        .catch(error => {
            console.error('Error fetching analytics data:', error);
            // Fallback to mock data in case of error
            //const mockData = generateMockAnalyticsData(startDate, endDate, workflowId);
            //updateAnalyticsDashboard(mockData);
        });
}

// Helper function to format date for API requests
// function TimezoneUtils.formatDate(date) {
//     return date.toISOString().split('T')[0]; // YYYY-MM-DD format
// }

function formatDate2(date) {
    const d = new Date(date); // Ensure it's a Date object
    return d.toISOString().split('T')[0];
}


// Generate mock analytics data for demonstration
function generateMockAnalyticsData(startDate, endDate, workflowId) {
    console.log('Generating mock analytics data...');
    
    // Generate date range
    const dateRange = [];
    const currentDate = new Date(startDate);
    while (currentDate <= endDate) {
        dateRange.push(new Date(currentDate));
        currentDate.setDate(currentDate.getDate() + 1);
    }
    
    // Generate random execution counts
    const executionCounts = {
        labels: dateRange.map(date => TimezoneUtils.formatDate(date)),
        datasets: [
            {
                label: 'Completed',
                data: dateRange.map(() => Math.floor(Math.random() * 20) + 5),
                backgroundColor: 'rgba(40, 167, 69, 0.2)',
                borderColor: '#28a745',
                borderWidth: 2,
                fill: true
            },
            {
                label: 'Failed',
                data: dateRange.map(() => Math.floor(Math.random() * 8) + 1),
                backgroundColor: 'rgba(220, 53, 69, 0.2)',
                borderColor: '#dc3545',
                borderWidth: 2,
                fill: true
            },
            {
                label: 'Running',
                data: dateRange.map(() => Math.floor(Math.random() * 5) + 1),
                backgroundColor: 'rgba(0, 123, 255, 0.2)',
                borderColor: '#007bff',
                borderWidth: 2,
                fill: true
            }
        ]
    };
    
    // Status distribution data
    const statusDistribution = {
        labels: ['Completed', 'Failed', 'Running', 'Paused', 'Cancelled'],
        datasets: [{
            data: [65, 15, 10, 5, 5],
            backgroundColor: [
                '#28a745', // Completed - Green
                '#dc3545', // Failed - Red
                '#007bff', // Running - Blue
                '#ffc107', // Paused - Yellow
                '#6c757d'  // Cancelled - Grey
            ],
            borderWidth: 1
        }]
    };
    
    // Generate workflow names if no specific workflow is selected
    const workflowNames = [
        'Data Processing', 'Report Generation', 
        'Approval Workflow', 'Customer Onboarding', 
        'Document Processing', 'Invoice Processing'
    ];
    
    // Top workflows data
    const topWorkflows = {
        labels: workflowId === 'all' ? workflowNames : [workflowNames[Math.floor(Math.random() * workflowNames.length)]],
        datasets: [{
            label: 'Execution Count',
            data: workflowId === 'all' ? 
                workflowNames.map(() => Math.floor(Math.random() * 100) + 20) :
                [Math.floor(Math.random() * 100) + 50],
            backgroundColor: '#36a2eb',
            borderWidth: 1
        }]
    };
    
    // Average duration data
    const durationData = {
        labels: workflowId === 'all' ? workflowNames : [workflowNames[Math.floor(Math.random() * workflowNames.length)]],
        datasets: [{
            label: 'Average Duration (seconds)',
            data: workflowId === 'all' ? 
                workflowNames.map(() => Math.floor(Math.random() * 300) + 30) :
                [Math.floor(Math.random() * 300) + 60],
            backgroundColor: '#ff9f40',
            borderWidth: 1
        }]
    };
    
    // Performance table data
    let tableData = [];
    if (workflowId === 'all') {
        workflowNames.forEach((name, index) => {
            const executions = Math.floor(Math.random() * 100) + 20;
            const successRate = Math.floor(Math.random() * 30) + 70; // 70-100%
            const avgDuration = Math.floor(Math.random() * 300) + 30; // 30-330 seconds
            const lastExecution = new Date();
            lastExecution.setHours(lastExecution.getHours() - Math.floor(Math.random() * 48));
            
            tableData.push({
                name: name,
                executions: executions,
                successRate: successRate,
                avgDuration: avgDuration,
                lastExecution: lastExecution.toISOString(),
                trendData: [
                    Math.random() * 100,
                    Math.random() * 100,
                    Math.random() * 100,
                    Math.random() * 100,
                    Math.random() * 100
                ]
            });
        });
    } else {
        const selectedWorkflow = workflowNames[Math.floor(Math.random() * workflowNames.length)];
        const executions = Math.floor(Math.random() * 100) + 20;
        const successRate = Math.floor(Math.random() * 30) + 70; // 70-100%
        const avgDuration = Math.floor(Math.random() * 300) + 30; // 30-330 seconds
        const lastExecution = new Date();
        lastExecution.setHours(lastExecution.getHours() - Math.floor(Math.random() * 48));
        
        tableData.push({
            name: selectedWorkflow,
            executions: executions,
            successRate: successRate,
            avgDuration: avgDuration,
            lastExecution: lastExecution.toISOString(),
            trendData: [
                Math.random() * 100,
                Math.random() * 100,
                Math.random() * 100,
                Math.random() * 100,
                Math.random() * 100
            ]
        });
    }
    
    // Calculate overall metrics
    const totalExecutions = executionCounts.datasets[0].data.reduce((sum, val) => sum + val, 0) + 
                          executionCounts.datasets[1].data.reduce((sum, val) => sum + val, 0);
    const successCount = executionCounts.datasets[0].data.reduce((sum, val) => sum + val, 0);
    const overallSuccessRate = totalExecutions > 0 ? Math.round((successCount / totalExecutions) * 100) : 0;
    const overallAvgDuration = Math.floor(tableData.reduce((sum, item) => sum + item.avgDuration, 0) / tableData.length);
    const pendingApprovals = Math.floor(Math.random() * 10);
    
    return {
        executionTrends: executionCounts,
        statusDistribution: statusDistribution,
        topWorkflows: topWorkflows,
        durationData: durationData,
        performanceTable: tableData,
        overallMetrics: {
            totalExecutions: totalExecutions,
            successRate: overallSuccessRate,
            avgDuration: overallAvgDuration,
            pendingApprovals: pendingApprovals
        }
    };
}

// Show loading state for charts and tables
function showLoadingState() {
    // Update the counters with loading indicators
    document.getElementById('total-executions-count').innerHTML = '<i class="bi bi-hourglass-split"></i>';
    document.getElementById('success-rate').innerHTML = '<i class="bi bi-hourglass-split"></i>';
    document.getElementById('avg-duration').innerHTML = '<i class="bi bi-hourglass-split"></i>';
    document.getElementById('pending-approvals-analytics').innerHTML = '<i class="bi bi-hourglass-split"></i>';
    
    // Show loading state in the performance table
    document.getElementById('performance-table-body').innerHTML = `
        <tr>
            <td colspan="6" class="text-center">
                <div class="spinner-border spinner-border-sm text-primary" role="status">
                    <span class="sr-only">Loading...</span>
                </div>
                Loading data...
            </td>
        </tr>
    `;
    
    // If charts already exist, destroy them so we can create new ones
    if (executionTrendChart) executionTrendChart.destroy();
    if (statusDistributionChart) statusDistributionChart.destroy();
    if (topWorkflowsChart) topWorkflowsChart.destroy();
    if (durationChart) durationChart.destroy();
}

// Update the analytics dashboard with the provided data
function updateAnalyticsDashboard(data) {
    console.log('Updating analytics dashboard with data:', data);
    performanceData = data.performanceTable;
    
    // Update the overview metrics
    document.getElementById('total-executions-count').textContent = data.overallMetrics.totalExecutions;
    document.getElementById('success-rate').textContent = `${data.overallMetrics.successRate}%`;
    document.getElementById('avg-duration').textContent = formatDuration(data.overallMetrics.avgDuration);
    document.getElementById('pending-approvals-analytics').textContent = data.overallMetrics.pendingApprovals;
    
    // Create/update execution trend chart
    const executionTrendCtx = document.getElementById('execution-trend-chart').getContext('2d');
    executionTrendChart = createExecutionTrendChart(executionTrendCtx, data.executionTrends);
    
    // Create/update status distribution chart
    const statusDistributionCtx = document.getElementById('status-distribution-chart').getContext('2d');
    statusDistributionChart = createStatusDistributionChart(statusDistributionCtx, data.statusDistribution);
    
    // Create/update top workflows chart
    const topWorkflowsCtx = document.getElementById('top-workflows-chart').getContext('2d');
    topWorkflowsChart = createTopWorkflowsChart(topWorkflowsCtx, data.topWorkflows);
    
    // Create/update duration chart
    const durationCtx = document.getElementById('duration-chart').getContext('2d');
    durationChart = createDurationChart(durationCtx, data.durationData);
    
    // Update performance table
    updatePerformanceTable(data.performanceTable);
}

// Create execution trend chart
function createExecutionTrendChart(ctx, data) {
    if (executionTrendChart) {
        executionTrendChart.destroy();
    }
    
    return new Chart(ctx, {
        type: 'line',
        data: data,
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    display: true,
                    title: {
                        display: true,
                        text: 'Date'
                    }
                },
                y: {
                    display: true,
                    title: {
                        display: true,
                        text: 'Execution Count'
                    },
                    beginAtZero: true
                }
            },
            plugins: {
                legend: {
                    position: 'top',
                },
                tooltip: {
                    mode: 'index',
                    intersect: false
                }
            }
        }
    });
}

// Create status distribution chart
function createStatusDistributionChart(ctx, data) {
    if (statusDistributionChart) {
        statusDistributionChart.destroy();
    }
    
    return new Chart(ctx, {
        type: 'doughnut',
        data: data,
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'right',
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            const label = context.label || '';
                            const value = context.raw || 0;
                            const total = context.dataset.data.reduce((sum, val) => sum + val, 0);
                            const percentage = Math.round((value / total) * 100);
                            return `${label}: ${value} (${percentage}%)`;
                        }
                    }
                }
            }
        }
    });
}

// Create top workflows chart
function createTopWorkflowsChart(ctx, data) {
    if (topWorkflowsChart) {
        topWorkflowsChart.destroy();
    }
    
    return new Chart(ctx, {
        type: 'bar',
        data: data,
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y',
            scales: {
                x: {
                    beginAtZero: true,
                    title: {
                        display: true,
                        text: 'Execution Count'
                    }
                }
            },
            plugins: {
                legend: {
                    display: false
                }
            }
        }
    });
}

// Create duration chart
function createDurationChart(ctx, data) {
    if (durationChart) {
        durationChart.destroy();
    }
    
    return new Chart(ctx, {
        type: 'bar',
        data: data,
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y',
            scales: {
                x: {
                    beginAtZero: true,
                    title: {
                        display: true,
                        text: 'Average Duration (seconds)'
                    }
                }
            },
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            const value = context.raw || 0;
                            return `Average: ${formatDuration(value)}`;
                        }
                    }
                }
            }
        }
    });
}

// Update performance table
function updatePerformanceTable(data) {
    const tableBody = document.getElementById('performance-table-body');
    tableBody.innerHTML = '';
    
    if (data.length === 0) {
        tableBody.innerHTML = `
            <tr>
                <td colspan="6" class="text-center">No data available</td>
            </tr>
        `;
        return;
    }
    
    data.forEach(workflow => {
        const row = document.createElement('tr');
        
        // Format the last execution date
        const lastExecutionDate =TimezoneUtils.formatDate(workflow.lastExecution);
        const formattedDate = lastExecutionDate;
        
        // Create a mini sparkline chart for the trend
        const trendChartId = `trend-chart-${Math.random().toString(36).substring(2, 9)}`;
        
        row.innerHTML = `
            <td>${workflow.name}</td>
            <td>${workflow.executions}</td>
            <td>${workflow.successRate}%</td>
            <td>${formatDuration(workflow.avgDuration)}</td>
            <td>${formattedDate}</td>
            <td><canvas id="${trendChartId}" width="100" height="30"></canvas></td>
        `;
        
        tableBody.appendChild(row);
        
        // Create the sparkline chart
        createSparklineChart(trendChartId, workflow.trendData);
    });
}

// Create a mini sparkline chart
function createSparklineChart(chartId, data) {
    const ctx = document.getElementById(chartId).getContext('2d');
    
    new Chart(ctx, {
        type: 'line',
        data: {
            labels: Array(data.length).fill(''),
            datasets: [{
                data: data,
                borderColor: '#007bff',
                borderWidth: 1.5,
                fill: false,
                pointRadius: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    enabled: false
                }
            },
            scales: {
                x: {
                    display: false
                },
                y: {
                    display: false,
                    min: 0
                }
            },
            elements: {
                line: {
                    tension: 0.4
                }
            }
        }
    });
}

// Format duration in seconds to a readable format
function formatDuration(seconds) {
    if (seconds < 60) {
        return `${seconds}s`;
    } else if (seconds < 3600) {
        const minutes = Math.floor(seconds / 60);
        const remainingSeconds = seconds % 60;
        return `${minutes}m ${remainingSeconds}s`;
    } else {
        const hours = Math.floor(seconds / 3600);
        const remainingMinutes = Math.floor((seconds % 3600) / 60);
        return `${hours}h ${remainingMinutes}m`;
    }
}

// Export performance data to CSV
function exportPerformanceData() {
    if (!performanceData || performanceData.length === 0) {
        alert('No data available to export');
        return;
    }
    
    // Create CSV content
    let csvContent = 'Workflow,Executions,Success Rate,Average Duration,Last Execution\n';
    
    performanceData.forEach(workflow => {
        const lastExecutionDate =TimezoneUtils.formatDate(workflow.lastExecution);
        csvContent += `"${workflow.name}",${workflow.executions},${workflow.successRate}%,${formatDuration(workflow.avgDuration)},"${lastExecutionDate}"\n`;
    });
    
    // Create a download link
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.setAttribute('href', url);
    link.setAttribute('download', 'workflow_performance.csv');
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    
    // Clean up the URL object
    setTimeout(() => {
        URL.revokeObjectURL(url);
    }, 100);
}


// Initialize logs functionality
function initLogsTab() {
    console.log('Initializing logs tab');
    
    // Set up event handlers
    document.getElementById('refresh-logs-btn').addEventListener('click', fetchWorkflowLogs);
    document.getElementById('export-logs-btn').addEventListener('click', exportWorkflowLogs);
    document.getElementById('log-execution-select').addEventListener('change', function() {
        logsFilter.execution_id = this.value;
        fetchWorkflowLogs();
    });
    document.getElementById('log-level-filter').addEventListener('change', function() {
        logsFilter.level = this.value;
        fetchWorkflowLogs();
    });
    document.getElementById('log-search-filter').addEventListener('input', debounce(function() {
        logsFilter.search = this.value;
        fetchWorkflowLogs();
    }, 500));
    document.getElementById('log-date-from').addEventListener('change', function() {
        logsFilter.dateFrom = this.value;
        fetchWorkflowLogs();
    });
    document.getElementById('log-date-to').addEventListener('change', function() {
        logsFilter.dateTo = this.value;
        fetchWorkflowLogs();
    });
    document.getElementById('logs-page-size').addEventListener('change', function() {
        logsPageSize = parseInt(this.value);
        logsCurrentPage = 1;
        fetchWorkflowLogs();
    });

    // Set today as the default "to" date
    const today = new Date();
    document.getElementById('log-date-to').valueAsDate = today;
    
    // Set 7 days ago as the default "from" date
    const sevenDaysAgo = new Date();
    sevenDaysAgo.setDate(today.getDate() - 7);
    document.getElementById('log-date-from').valueAsDate = sevenDaysAgo;
    
    logsFilter.dateFrom = formatDateForFilter(sevenDaysAgo);
    logsFilter.dateTo = formatDateForFilter(today);
    
    // Load execution IDs for the dropdown
    loadExecutionIds();
    
    // Initial fetch of logs
    fetchWorkflowLogs();
}

// Function to load execution IDs for the dropdown
function loadExecutionIds() {
    fetch('/api/workflow/executions?limit=100')
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success' && data.executions) {
                const executionSelect = document.getElementById('log-execution-select');
                // Keep the "All Executions" option
                executionSelect.innerHTML = '<option value="">All Executions</option>';
                
                // Add each execution as an option
                data.executions.forEach(execution => {
                    const option = document.createElement('option');
                    option.value = execution.execution_id;
                    
                    // Format the display text: Workflow name + date
                    const dateStr = TimezoneUtils.formatDate(execution.started_at);
                    option.textContent = `${execution.workflow_name || 'Workflow'} (${dateStr})`;
                    
                    // Add a class based on status for color coding
                    option.classList.add(`status-${execution.status.toLowerCase()}`);
                    
                    executionSelect.appendChild(option);
                });
            }
        })
        .catch(error => {
            console.error('Error loading executions:', error);
        });
}

// Function to fetch workflow logs with pagination and filtering
function fetchWorkflowLogs() {
    // Show loading indicator
    document.getElementById('logs-table-body').innerHTML = `
        <tr>
            <td colspan="6" class="text-center py-3">
                <div class="spinner-border spinner-border-sm text-primary" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
                <span class="ms-2">Loading logs...</span>
            </td>
        </tr>
    `;
    
    // Build the query URL with filters
    let url = '/api/workflow/logs?';
    
    // Add pagination
    url += `page=${logsCurrentPage}&pageSize=${logsPageSize}`;
    
    // Add filters if they exist
    if (logsFilter.execution_id) {
        url += `&execution_id=${encodeURIComponent(logsFilter.execution_id)}`;
    }
    if (logsFilter.level) {
        url += `&level=${encodeURIComponent(logsFilter.level)}`;
    }
    if (logsFilter.search) {
        url += `&search=${encodeURIComponent(logsFilter.search)}`;
    }
    if (logsFilter.dateFrom) {
        url += `&dateFrom=${encodeURIComponent(logsFilter.dateFrom)}`;
    }
    if (logsFilter.dateTo) {
        url += `&dateTo=${encodeURIComponent(logsFilter.dateTo)}`;
    }
    
    // Fetch logs from the API
    fetch(url)
        .then(response => {
            if (!response.ok) {
                throw new Error('Network response was not ok');
            }
            return response.json();
        })
        .then(data => {
            handleLogsResponse(data);
        })
        .catch(error => {
            console.error('Error fetching workflow logs:', error);
            document.getElementById('logs-table-body').innerHTML = `
                <tr>
                    <td colspan="6" class="text-center text-danger">
                        Error loading logs: ${error.message}
                    </td>
                </tr>
            `;
        });
}

// Function to handle the logs API response
function handleLogsResponse(data) {
    if (data.status !== 'success') {
        console.error('API returned error:', data.message);
        document.getElementById('logs-table-body').innerHTML = `
            <tr>
                <td colspan="6" class="text-center text-danger">
                    ${data.message || 'Error loading logs'}
                </td>
            </tr>
        `;
        return;
    }
    
    logsData = data.logs || [];
    logsTotalPages = data.pagination ? data.pagination.total_pages : 1;
    
    // Update the logs table
    renderLogsTable();
    
    // Update pagination
    renderLogsPagination();
}

// Function to render logs table
function renderLogsTable() {
    const tableBody = document.getElementById('logs-table-body');
    
    // Clear the table
    tableBody.innerHTML = '';
    
    if (logsData.length === 0) {
        tableBody.innerHTML = `
            <tr>
                <td colspan="6" class="text-center">
                    No logs found matching your criteria
                </td>
            </tr>
        `;
        return;
    }
    
    // Create a row for each log entry
    logsData.forEach(log => {
        const row = document.createElement('tr');
        
        // Format timestamp
        const timestamp = TimezoneUtils.formatDate(log.timestamp);
        const formattedTimestamp = timestamp.toLocaleString();
        
        // Determine level badge class
        let levelClass = 'secondary';
        switch (log.log_level.toLowerCase()) {
            case 'info': levelClass = 'info'; break;
            case 'warning': levelClass = 'warning'; break;
            case 'error': levelClass = 'danger'; break;
            case 'debug': levelClass = 'secondary'; break;
        }
        
        // Prepare log message (truncate if too long)
        let message = log.message;
        const maxMessageLength = 100;
        const isTruncated = message.length > maxMessageLength;
        if (isTruncated) {
            message = message.substring(0, maxMessageLength) + '...';
        }
        
        // Get node name if available
        const nodeName = log.node_name || 'N/A';
        
        // Build the row HTML
        row.innerHTML = `
            <td>${formattedTimestamp}</td>
            <td><span class="badge bg-${levelClass}">${log.log_level}</span></td>
            <td class="text-wrap text-break">${message}</td>
            <td class="text-muted small">${log.execution_id.substring(0, 8)}...</td>
            <td>${nodeName}</td>
            <td>
                <button class="btn btn-sm btn-outline-info view-log-btn" data-log-index="${logsData.indexOf(log)}">
                    <i class="bi bi-eye"></i>
                </button>
            </td>
        `;
        
        tableBody.appendChild(row);
    });
    
    // Add event listeners to view buttons
    document.querySelectorAll('.view-log-btn').forEach(button => {
        button.addEventListener('click', function() {
            const logIndex = parseInt(this.getAttribute('data-log-index'));
            showLogDetails(logsData[logIndex]);
        });
    });
}

// Function to render pagination controls
function renderLogsPagination() {
    const paginationElement = document.getElementById('logs-pagination');
    const ul = paginationElement.querySelector('ul');
    
    // Clear current pagination
    ul.innerHTML = '';
    
    // Previous button
    const prevLi = document.createElement('li');
    prevLi.className = `page-item ${logsCurrentPage === 1 ? 'disabled' : ''}`;
    prevLi.innerHTML = `
        <a class="page-link" href="#" aria-label="Previous">
            <span aria-hidden="true">&laquo;</span>
        </a>
    `;
    if (logsCurrentPage > 1) {
        prevLi.addEventListener('click', function(e) {
            e.preventDefault();
            if (logsCurrentPage > 1) {
                logsCurrentPage--;
                fetchWorkflowLogs();
            }
        });
    }
    ul.appendChild(prevLi);
    
    // Page numbers
    const maxVisiblePages = 5;
    let startPage = Math.max(1, logsCurrentPage - Math.floor(maxVisiblePages / 2));
    let endPage = Math.min(logsTotalPages, startPage + maxVisiblePages - 1);
    
    // Adjust start page if we're near the end
    if (endPage - startPage + 1 < maxVisiblePages && startPage > 1) {
        startPage = Math.max(1, endPage - maxVisiblePages + 1);
    }
    
    // Add first page and ellipsis if needed
    if (startPage > 1) {
        const firstLi = document.createElement('li');
        firstLi.className = 'page-item';
        firstLi.innerHTML = `<a class="page-link" href="#">1</a>`;
        firstLi.addEventListener('click', function(e) {
            e.preventDefault();
            logsCurrentPage = 1;
            fetchWorkflowLogs();
        });
        ul.appendChild(firstLi);
        
        if (startPage > 2) {
            const ellipsisLi = document.createElement('li');
            ellipsisLi.className = 'page-item disabled';
            ellipsisLi.innerHTML = `<a class="page-link" href="#">...</a>`;
            ul.appendChild(ellipsisLi);
        }
    }
    
    // Add page numbers
    for (let i = startPage; i <= endPage; i++) {
        const pageLi = document.createElement('li');
        pageLi.className = `page-item ${i === logsCurrentPage ? 'active' : ''}`;
        pageLi.innerHTML = `<a class="page-link" href="#">${i}</a>`;
        pageLi.addEventListener('click', function(e) {
            e.preventDefault();
            logsCurrentPage = i;
            fetchWorkflowLogs();
        });
        ul.appendChild(pageLi);
    }
    
    // Add last page and ellipsis if needed
    if (endPage < logsTotalPages) {
        if (endPage < logsTotalPages - 1) {
            const ellipsisLi = document.createElement('li');
            ellipsisLi.className = 'page-item disabled';
            ellipsisLi.innerHTML = `<a class="page-link" href="#">...</a>`;
            ul.appendChild(ellipsisLi);
        }
        
        const lastLi = document.createElement('li');
        lastLi.className = 'page-item';
        lastLi.innerHTML = `<a class="page-link" href="#">${logsTotalPages}</a>`;
        lastLi.addEventListener('click', function(e) {
            e.preventDefault();
            logsCurrentPage = logsTotalPages;
            fetchWorkflowLogs();
        });
        ul.appendChild(lastLi);
    }
    
    // Next button
    const nextLi = document.createElement('li');
    nextLi.className = `page-item ${logsCurrentPage === logsTotalPages ? 'disabled' : ''}`;
    nextLi.innerHTML = `
        <a class="page-link" href="#" aria-label="Next">
            <span aria-hidden="true">&raquo;</span>
        </a>
    `;
    if (logsCurrentPage < logsTotalPages) {
        nextLi.addEventListener('click', function(e) {
            e.preventDefault();
            if (logsCurrentPage < logsTotalPages) {
                logsCurrentPage++;
                fetchWorkflowLogs();
            }
        });
    }
    ul.appendChild(nextLi);
}

// Function to show log details in a modal
function showLogDetails(log) {
    // Set the log details in the modal
    document.getElementById('detail-timestamp').textContent = TimezoneUtils.formatDate(log.timestamp);
    
    const levelBadge = document.getElementById('detail-level-badge');
    levelBadge.textContent = log.log_level;
    
    // Set badge color
    levelBadge.className = 'badge';
    switch (log.log_level.toLowerCase()) {
        case 'info': levelBadge.classList.add('bg-info'); break;
        case 'warning': levelBadge.classList.add('bg-warning'); break;
        case 'error': levelBadge.classList.add('bg-danger'); break;
        case 'debug': levelBadge.classList.add('bg-secondary'); break;
    }
    
    document.getElementById('detail-execution-id').textContent = log.execution_id;
    document.getElementById('detail-step').textContent = log.node_name || 'N/A';
    document.getElementById('detail-message').textContent = log.message;
    
    // Handle details JSON if present
    const detailsContainer = document.getElementById('detail-json-container');
    const detailsContent = document.getElementById('detail-json');
    
    if (log.details) {
        let detailsObj;
        try {
            // If details is a string, try to parse it as JSON
            if (typeof log.details === 'string') {
                detailsObj = JSON.parse(log.details);
            } else {
                detailsObj = log.details;
            }
            detailsContent.textContent = JSON.stringify(detailsObj, null, 2);
            detailsContainer.style.display = 'block';
        } catch (e) {
            // If not valid JSON, just display as string
            detailsContent.textContent = log.details;
            detailsContainer.style.display = 'block';
        }
    } else {
        detailsContainer.style.display = 'none';
    }
    
    // Show the modal
    const modal = new bootstrap.Modal(document.getElementById('logDetailsModal'));
    modal.show();
}

// Function to export logs to CSV
function exportWorkflowLogs() {
    // Start with column headers
    let csvContent = "data:text/csv;charset=utf-8,";
    csvContent += "Timestamp,Level,Message,Execution ID,Node\n";
    
    // Add each log entry
    logsData.forEach(log => {
        const timestamp = TimezoneUtils.formatDate(log.timestamp);
        
        // Escape fields for CSV (replace commas and quotes)
        const message = `"${log.message.replace(/"/g, '""')}"`;
        const nodeName = log.node_name ? `"${log.node_name.replace(/"/g, '""')}"` : "N/A";
        
        csvContent += `${timestamp},${log.log_level},${message},${log.execution_id},${nodeName}\n`;
    });
    
    // Create download link
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `workflow_logs_${new Date().toISOString().slice(0, 10)}.csv`);
    document.body.appendChild(link);
    
    // Trigger download and clean up
    link.click();
    document.body.removeChild(link);
}

// Utility function to format date for filter
function formatDateForFilter(date) {
    return date.toISOString().split('T')[0]; // Returns YYYY-MM-DD
}

// Utility function to debounce input events
function debounce(func, wait, immediate) {
    let timeout;
    return function() {
        const context = this, args = arguments;
        const later = function() {
            timeout = null;
            if (!immediate) func.apply(context, args);
        };
        const callNow = immediate && !timeout;
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
        if (callNow) func.apply(context, args);
    };
}






// Load all workflow schedules
function loadWorkflowSchedules() {
    const container = document.getElementById('workflowSchedulesList');
    const loadingIndicator = document.getElementById('schedulesLoadingIndicator');
    
    if (!container || !loadingIndicator) return;
    
    // Show loading indicator
    loadingIndicator.style.display = 'block';
    container.innerHTML = '';
    const schedule_type = 'workflow';
    fetch(`/api/scheduler/types/${schedule_type}/schedules`)
        .then(response => response.json())
        .then(data => {
            // Hide loading indicator
            loadingIndicator.style.display = 'none';
            
            if (data.length === 0) {
                container.innerHTML = '<div class="alert alert-info">No schedules found for any workflows. Click "Add Schedule" to create one.</div>';
                return;
            }
            
            // Group schedules by workflow
            const schedulesByWorkflow = {};
            
            data.forEach(schedule => {
                if (!schedulesByWorkflow[schedule.workflow_id]) {
                    schedulesByWorkflow[schedule.workflow_id] = {
                        workflowName: schedule.workflow_name || `Workflow ${schedule.workflow_id}`,
                        schedules: []
                    };
                }
                
                schedulesByWorkflow[schedule.workflow_id].schedules.push(schedule);
            });
            
            // Create accordion for workflows
            let html = '<div class="accordion" id="workflowSchedulesAccordion">';
            
            let index = 0;
            for (const [workflowId, workflowData] of Object.entries(schedulesByWorkflow)) {
                const headingId = `heading-${workflowId}`;
                const collapseId = `collapse-${workflowId}`;
                
                html += `
                    <div class="accordion-item">
                        <h2 class="accordion-header" id="${headingId}">
                            <button class="accordion-button ${index > 0 ? 'collapsed' : ''}" type="button" 
                                    data-bs-toggle="collapse" data-bs-target="#${collapseId}" 
                                    aria-expanded="${index === 0 ? 'true' : 'false'}" aria-controls="${collapseId}">
                                ${workflowData.workflowName} (${workflowData.schedules.length} schedule${workflowData.schedules.length !== 1 ? 's' : ''})
                            </button>
                        </h2>
                        <div id="${collapseId}" class="accordion-collapse collapse ${index === 0 ? 'show' : ''}" 
                             aria-labelledby="${headingId}" data-bs-parent="#workflowSchedulesAccordion">
                            <div class="accordion-body p-0">
                                <div class="list-group list-group-flush">
                `;
                
                // Add each schedule for this workflow
                workflowData.schedules.forEach(schedule => {
                    const scheduleDescription = getScheduleDescription(schedule);
                    const activeStatus = schedule.is_active ? 
                        '<span class="badge bg-success">Active</span>' : 
                        '<span class="badge bg-secondary">Inactive</span>';
                    
                    let nextRun = schedule.next_run_time ? 
                        `Next run: ${formatDateTime(new Date(schedule.next_run_time))}` : 
                        'No future runs scheduled';
                    
                    let lastRun = schedule.last_run_time ? 
                        `Last run: ${formatDateTime(new Date(schedule.last_run_time))}` : 
                        'Never run';
                    
                    let runsInfo = '';
                    if (schedule.max_runs) {
                        runsInfo = `${schedule.current_runs || 0}/${schedule.max_runs} executions completed`;
                    }
                    
                    html += `
                        <div class="list-group-item">
                            <div class="d-flex w-100 justify-content-between">
                                <h6 class="mb-1">${scheduleDescription}</h6>
                                ${activeStatus}
                            </div>
                            <p class="mb-1">${nextRun}</p>
                            <small>${lastRun}</small>
                            ${runsInfo ? `<small class="d-block">${runsInfo}</small>` : ''}
                            <div class="mt-2">
                                <button type="button" class="btn btn-outline-primary btn-sm" 
                                        onclick="editWorkflowSchedule(${schedule.id}, ${schedule.workflow_id})">
                                    <i class="bi bi-pencil"></i> Edit
                                </button>
                                <!-- 
                                <button type="button" class="btn btn-outline-success btn-sm" 
                                        onclick="runWorkflowNow(${schedule.workflow_id})">
                                    <i class="bi bi-play-fill"></i> Run Now
                                </button>
                                -->
                                <button type="button" class="btn btn-outline-danger btn-sm" 
                                        onclick="deleteWorkflowSchedule(${schedule.id}, ${schedule.workflow_id})">
                                    <i class="bi bi-trash"></i> Delete
                                </button>
                            </div>
                        </div>
                    `;
                });
                
                html += `
                                </div>
                            </div>
                        </div>
                    </div>
                `;
                
                index++;
            }
            
            html += '</div>';
            container.innerHTML = html;
        })
        .catch(error => {
            console.error('Error loading workflow schedules:', error);
            loadingIndicator.style.display = 'none';
            container.innerHTML = '<div class="alert alert-danger">Failed to load schedules. Please try again.</div>';
        });
}

// Load workflows for dropdown
function loadWorkflowsForDropdown() {
    const workflowSelect = document.getElementById('workflowSelect');
    if (!workflowSelect) return;
    
    // Show loading state
    workflowSelect.disabled = true;
    workflowSelect.innerHTML = '<option value="">Loading workflows...</option>';
    
    fetch('/get/workflows')
        .then(response => response.json())
        .then(data => {
            // Parse the data if it's a string
            const workflows = typeof data === 'string' ? JSON.parse(data) : data;
            
            // Reset dropdown
            workflowSelect.innerHTML = '<option value="">-- Select a workflow --</option>';
            
            // Process the data based on structure
            if (Array.isArray(workflows)) {
                workflows.forEach(workflow => {
                    const option = document.createElement('option');
                    option.value = workflow.id || workflow.ID;
                    option.textContent = workflow.workflow_name || workflow.name || 'Unnamed';
                    workflowSelect.appendChild(option);
                });
            } else {
                // If it's an object with numeric keys (like from pandas DataFrame)
                Object.values(workflows).forEach(workflow => {
                    const option = document.createElement('option');
                    option.value = workflow.id || workflow.ID;
                    option.textContent = workflow.workflow_name || workflow.name || 'Unnamed';
                    workflowSelect.appendChild(option);
                });
            }
            
            workflowSelect.disabled = false;
        })
        .catch(error => {
            console.error('Error loading workflows:', error);
            workflowSelect.innerHTML = '<option value="">Error loading workflows</option>';
            workflowSelect.disabled = false;
        });
}

// Toggle schedule type fields
function toggleScheduleTypeFields() {
    const scheduleType = document.getElementById('scheduleType').value;
    
    // Hide all settings sections
    document.getElementById('intervalSettings').style.display = 'none';
    document.getElementById('cronSettings').style.display = 'none';
    
    // Show appropriate section based on type
    if (scheduleType === 'interval') {
        document.getElementById('intervalSettings').style.display = 'block';
    } else if (scheduleType === 'cron') {
        document.getElementById('cronSettings').style.display = 'block';
    }
}

// Toggle edit schedule type fields
function toggleEditScheduleTypeFields() {
    const scheduleType = document.getElementById('editScheduleType').value;
    
    // Hide all settings sections
    document.getElementById('editIntervalSettings').style.display = 'none';
    document.getElementById('editCronSettings').style.display = 'none';
    
    // Show appropriate section based on type
    if (scheduleType === 'interval') {
        document.getElementById('editIntervalSettings').style.display = 'block';
    } else if (scheduleType === 'cron') {
        document.getElementById('editCronSettings').style.display = 'block';
    }
}

// Save a new schedule
function saveWorkflowSchedule() {
    const workflowId = document.getElementById('workflowSelect').value;
    const scheduleType = document.getElementById('scheduleType').value;
    
    if (!workflowId) {
        showAlert('warning', 'Please select a workflow.');
        return;
    }
    
    if (!scheduleType) {
        showAlert('warning', 'Please select a schedule type.');
        return;
    }
    
    // Build schedule data based on type
    const scheduleData = {
        type: scheduleType,
        start_date: document.getElementById('startDate').value || null,
        end_date: document.getElementById('endDate').value || null,
        max_runs: document.getElementById('maxRuns').value ? parseInt(document.getElementById('maxRuns').value) : null,
        is_active: document.getElementById('isActive').checked,
        // Add timezone offset in minutes
        timezone_offset: new Date().getTimezoneOffset()
    };
    
    if (scheduleType === 'interval') {
        scheduleData.interval_seconds = document.getElementById('intervalSeconds').value ? parseInt(document.getElementById('intervalSeconds').value) : null;
        scheduleData.interval_minutes = document.getElementById('intervalMinutes').value ? parseInt(document.getElementById('intervalMinutes').value) : null;
        scheduleData.interval_hours = document.getElementById('intervalHours').value ? parseInt(document.getElementById('intervalHours').value) : null;
        scheduleData.interval_days = document.getElementById('intervalDays').value ? parseInt(document.getElementById('intervalDays').value) : null;
        scheduleData.interval_weeks = document.getElementById('intervalWeeks').value ? parseInt(document.getElementById('intervalWeeks').value) : null;
        
        // Validate that at least one interval is set
        if (!scheduleData.interval_seconds && !scheduleData.interval_minutes && 
            !scheduleData.interval_hours && !scheduleData.interval_days && !scheduleData.interval_weeks) {
            showAlert('warning', 'Please specify at least one interval value.');
            return;
        }
    } else if (scheduleType === 'cron') {
        scheduleData.cron_expression = document.getElementById('cronExpression').value;
        
        if (!scheduleData.cron_expression) {
            showAlert('warning', 'Please enter a cron expression.');
            return;
        }
    } else if (scheduleType === 'date') {
        if (!scheduleData.start_date) {
            showAlert('warning', 'Please specify a start date for one-time execution.');
            return;
        }
    }
    
    // Create API endpoint for workflow schedules
    const schedule_type = 'workflow';
    fetch(`/api/scheduler/jobs/${workflowId}/types/${schedule_type}/schedules`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(scheduleData)
    })
    .then(response => response.json())
    .then(data => {
        // Hide modal
        const modal = bootstrap.Modal.getInstance(document.getElementById('addWorkflowScheduleModal'));
        modal.hide();
        
        // Show success message
        showAlert('success', 'Schedule created successfully.');
        
        // Reload schedules
        loadWorkflowSchedules();
    })
    .catch(error => {
        console.error('Error creating schedule:', error);
        showAlert('danger', `Failed to create schedule: ${error.message}`);
    });
}

// Edit an existing schedule
function editWorkflowSchedule(scheduleId, workflowId) {
    const schedule_type = 'workflow';
    fetch(`/api/scheduler/jobs/${workflowId}/types/${schedule_type}/schedules/${scheduleId}`)
        .then(response => response.json())
        .then(schedule => {
            // Get workflow name
            fetch(`/get/workflow/${workflowId}`)
                .then(response => response.json())
                .then(workflow => {
                    const workflowData = typeof workflow === 'string' ? JSON.parse(workflow) : workflow;
                    const workflowName = workflowData.workflow_name || workflowData.name || `Workflow ${workflowId}`;
                    
                    // Populate form fields
                    document.getElementById('editScheduleId').value = scheduleId;
                    document.getElementById('editScheduleWorkflowId').value = workflowId;
                    document.getElementById('editWorkflowName').value = workflowName;
                    document.getElementById('editScheduleType').value = schedule.type;
                    
                    // Trigger change event to show appropriate fields
                    toggleEditScheduleTypeFields();
                    
                    // Interval settings
                    document.getElementById('editIntervalSeconds').value = schedule.interval_seconds || '';
                    document.getElementById('editIntervalMinutes').value = schedule.interval_minutes || '';
                    document.getElementById('editIntervalHours').value = schedule.interval_hours || '';
                    document.getElementById('editIntervalDays').value = schedule.interval_days || '';
                    document.getElementById('editIntervalWeeks').value = schedule.interval_weeks || '';
                    
                    // Cron settings
                    document.getElementById('editCronExpression').value = schedule.cron_expression || '';
                    
                    // Format dates for datetime-local input
                    const formatDateForInput = (dateString) => {
                        if (!dateString) return '';
                        const date = new Date(dateString);
                        return date.toISOString().slice(0, 16); // Format as YYYY-MM-DDTHH:MM
                    };
                    
                    // Date settings
                    document.getElementById('editStartDate').value = schedule.start_date ? convertUtcToLocal(new Date(normalizeUtcDateString(schedule.start_date))) : '';
                    document.getElementById('editEndDate').value = schedule.end_date ? convertUtcToLocal(new Date(normalizeUtcDateString(schedule.end_date))) : '';

                    // Other settings
                    document.getElementById('editMaxRuns').value = schedule.max_runs || '';
                    document.getElementById('editIsActive').checked = schedule.is_active;
                    
                    // Show modal
                    const modal = new bootstrap.Modal(document.getElementById('editWorkflowScheduleModal'));
                    modal.show();
                })
                .catch(error => {
                    console.error('Error getting workflow details:', error);
                    showAlert('danger', 'Failed to load workflow details.');
                });
        })
        .catch(error => {
            console.error('Error loading schedule details:', error);
            showAlert('danger', 'Failed to load schedule details.');
        });
}

// Update an existing schedule
function updateWorkflowSchedule() {
    const scheduleId = document.getElementById('editScheduleId').value;
    const workflowId = document.getElementById('editScheduleWorkflowId').value;
    const scheduleType = document.getElementById('editScheduleType').value;
    
    // Build schedule data based on type
    const scheduleData = {
        start_date: document.getElementById('editStartDate').value || null,
        end_date: document.getElementById('editEndDate').value || null,
        max_runs: document.getElementById('editMaxRuns').value ? parseInt(document.getElementById('editMaxRuns').value) : null,
        is_active: document.getElementById('editIsActive').checked,
        // Add timezone offset in minutes
        timezone_offset: new Date().getTimezoneOffset()
    };
    
    if (scheduleType === 'interval') {
        scheduleData.interval_seconds = document.getElementById('editIntervalSeconds').value ? parseInt(document.getElementById('editIntervalSeconds').value) : null;
        scheduleData.interval_minutes = document.getElementById('editIntervalMinutes').value ? parseInt(document.getElementById('editIntervalMinutes').value) : null;
        scheduleData.interval_hours = document.getElementById('editIntervalHours').value ? parseInt(document.getElementById('editIntervalHours').value) : null;
        scheduleData.interval_days = document.getElementById('editIntervalDays').value ? parseInt(document.getElementById('editIntervalDays').value) : null;
        scheduleData.interval_weeks = document.getElementById('editIntervalWeeks').value ? parseInt(document.getElementById('editIntervalWeeks').value) : null;
    } else if (scheduleType === 'cron') {
        scheduleData.cron_expression = document.getElementById('editCronExpression').value;
    }
    
    // Update schedule via API
    const schedule_type = 'workflow';
    fetch(`/api/scheduler/jobs/${workflowId}/types/${schedule_type}/schedules/${scheduleId}`, {
        method: 'PUT',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(scheduleData)
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(err => { throw new Error(err.error || `Server error (${response.status})`); });
        }
        return response.json();
    })
    .then(data => {
        // Hide modal
        const modal = bootstrap.Modal.getInstance(document.getElementById('editWorkflowScheduleModal'));
        modal.hide();
        
        // Show success message
        showAlert('success', 'Schedule updated successfully.');
        
        // Reload schedules
        loadWorkflowSchedules();
    })
    .catch(error => {
        console.error('Error updating schedule:', error);
        showAlert('danger', `Failed to update schedule: ${error.message}`);
    });
}

// Delete a schedule
function deleteWorkflowSchedule(scheduleId, workflowId) {
    if (!confirm('Are you sure you want to delete this schedule?')) {
        return;
    }
    const schedule_type = 'workflow';
    fetch(`/api/scheduler/jobs/${workflowId}/types/${schedule_type}/schedules/${scheduleId}`, {
        method: 'DELETE'
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(err => { throw new Error(err.error || `Server error (${response.status})`); });
        }
        return response.json();
    })
    .then(data => {
        // Show success message
        showAlert('success', 'Schedule deleted successfully.');
        
        // Reload schedules
        loadWorkflowSchedules();
    })
    .catch(error => {
        console.error('Error deleting schedule:', error);
        showAlert('danger', `Failed to delete schedule: ${error.message}`);
    });
}

function normalizeUtcDateString(dateStr) {
    // Add 'Z' if the string looks like a UTC datetime but lacks a timezone
    if (typeof dateStr === 'string' && !dateStr.endsWith('Z') && !dateStr.includes('+') && !dateStr.includes('T')) {
        // Basic date/time format like "2025-05-16 22:27:00"
        dateStr = dateStr.replace(' ', 'T') + 'Z';
    } else if (typeof dateStr === 'string' && dateStr.match(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/)) {
        dateStr += 'Z';
    }
    return dateStr;
}

// Converts a UTC date/time string or Date object to local time and formats it
function convertUtcToLocal(utcDate) {
    let date = typeof utcDate === 'string' ? new Date(utcDate) : utcDate;

    if (isNaN(date.getTime())) {
        throw new Error('Invalid date');
    }

    // Format using 24-hour time
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    
    return `${year}-${month}-${day} ${hours}:${minutes}`;
}

function convertUtcToLocalString(utcString) {
    const date = new Date(utcString);
    if (isNaN(date)) return '';
    
    // Format using 24-hour time
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    
    return `${year}-${month}-${day} ${hours}:${minutes}`;
}

// Run a workflow immediately
function runWorkflowNow(workflowId) {
    fetch(`/api/scheduler/run/workflow/${workflowId}`, {
        method: 'POST'
    })
    .then(response => response.json())
    .then(data => {
        // Show success message
        showAlert('success', 'Workflow execution started.');
    })
    .catch(error => {
        console.error('Error running workflow:', error);
        showAlert('danger', `Failed to run workflow: ${error.message}`);
    });
}

// Helper function to get schedule description
function getScheduleDescription(schedule) {
    if (schedule.type === 'interval') {
        const parts = [];
        
        if (schedule.interval_seconds) {
            parts.push(`${schedule.interval_seconds} second${schedule.interval_seconds !== 1 ? 's' : ''}`);
        }
        
        if (schedule.interval_minutes) {
            parts.push(`${schedule.interval_minutes} minute${schedule.interval_minutes !== 1 ? 's' : ''}`);
        }
        
        if (schedule.interval_hours) {
            parts.push(`${schedule.interval_hours} hour${schedule.interval_hours !== 1 ? 's' : ''}`);
        }
        
        if (schedule.interval_days) {
            parts.push(`${schedule.interval_days} day${schedule.interval_days !== 1 ? 's' : ''}`);
        }
        
        if (schedule.interval_weeks) {
            parts.push(`${schedule.interval_weeks} week${schedule.interval_weeks !== 1 ? 's' : ''}`);
        }
        
        return `Every ${parts.join(', ')}`;
    } else if (schedule.type === 'cron') {
        return `Cron: ${schedule.cron_expression}`;
    } else if (schedule.type === 'date') {
        return `One-time: ${
                            schedule.start_date
                                ? new Date(normalizeUtcDateString(schedule.start_date))
                                    .toLocaleString()
                                : ''
                            }
                            `;
    }
    
    return 'Unknown schedule type';
}

// Helper function to format datetime
function formatDateTime(date) {
    return date.toLocaleString();
}

// Convert UTC to local time for display
//const localDate = new Date(normalizeUtcDateString(schedule.start_date));
//scheduleDesc = `One-time: ${formatDateTime(localDate)}`;

// Helper function to show alert
function showAlert(type, message) {
    const alertContainer = document.createElement('div');
    alertContainer.className = `alert alert-${type} alert-dismissible fade show`;
    alertContainer.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
    `;
    
    // Find a good place to show the alert
    const container = document.querySelector('.content') || document.body;
    container.prepend(alertContainer);
    
    // Auto-dismiss after 5 seconds
    setTimeout(() => {
        const alertInstance = bootstrap.Alert.getInstance(alertContainer);
        if (alertInstance) {
            alertInstance.close();
        } else {
            alertContainer.remove();
        }
    }, 5000);
}
