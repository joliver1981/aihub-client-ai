/**
 * Page Context: Data Agent Builder (custom_data_agent)
 * 
 * Provides real-time context about the data agent configuration page.
 * Add this script to custom_data_agent.html
 */

window.assistantPageContext = {
    page: 'custom_data_agent',
    pageName: 'Data Agent Builder',
    
    getPageData: function() {
        const data = {
            agent: {
                id: null,
                name: '',
                objective: '',
                isNew: false
            },
            connection: {
                id: null,
                name: ''
            },
            agentsList: {
                total: 0,
                available: []
            },
            connectionsList: {
                total: 0,
                available: []
            },
            mode: 'edit', // 'edit' or 'create'
            validation: {
                isValid: true,
                errors: []
            },
            availableActions: []
        };
        
        // === DETERMINE MODE ===
        const agentSelectGroup = document.getElementById('agentSelectGroup');
        const agentNameGroup = document.getElementById('agentNameGroup');
        
        if (agentNameGroup && agentNameGroup.style.display !== 'none') {
            data.mode = 'create';
            data.agent.isNew = true;
        } else {
            data.mode = 'edit';
            data.agent.isNew = false;
        }
        
        // === AGENT INFO ===
        if (data.mode === 'create') {
            // New agent - get name from input field
            const agentNameInput = document.getElementById('agentName');
            data.agent.name = agentNameInput ? agentNameInput.value.trim() : '';
        } else {
            // Existing agent - get from dropdown
            const agentSelect = document.getElementById('agentSelect');
            if (agentSelect && agentSelect.value) {
                data.agent.id = agentSelect.value;
                const selectedOption = agentSelect.options[agentSelect.selectedIndex];
                data.agent.name = selectedOption ? selectedOption.text.trim() : '';
            }
        }
        
        // Get agent ID from hidden field
        const agentIdField = document.getElementById('agent_id');
        if (agentIdField && agentIdField.value) {
            data.agent.id = agentIdField.value;
        }
        
        // Get objective
        const objectiveField = document.getElementById('agentObjective');
        data.agent.objective = objectiveField ? objectiveField.value.trim() : '';
        
        // === CONNECTION INFO ===
        const connectionSelect = document.getElementById('connectionSelect');
        if (connectionSelect && connectionSelect.value) {
            data.connection.id = connectionSelect.value;
            const selectedOption = connectionSelect.options[connectionSelect.selectedIndex];
            data.connection.name = selectedOption ? selectedOption.text.trim() : '';
        }
        
        // === LIST ALL AVAILABLE AGENTS ===
        const agentSelect = document.getElementById('agentSelect');
        if (agentSelect) {
            for (let i = 0; i < agentSelect.options.length; i++) {
                const option = agentSelect.options[i];
                if (option.value) {
                    data.agentsList.available.push({
                        id: option.value,
                        name: option.text.trim()
                    });
                }
            }
            data.agentsList.total = data.agentsList.available.length;
        }
        
        // === LIST ALL AVAILABLE CONNECTIONS ===
        if (connectionSelect) {
            for (let i = 0; i < connectionSelect.options.length; i++) {
                const option = connectionSelect.options[i];
                if (option.value) {
                    data.connectionsList.available.push({
                        id: option.value,
                        name: option.text.trim()
                    });
                }
            }
            data.connectionsList.total = data.connectionsList.available.length;
        }
        
        // === VALIDATION ===
        if (!data.agent.name && data.mode === 'create') {
            data.validation.isValid = false;
            data.validation.errors.push('Agent name is required');
        }
        if (!data.agent.objective) {
            data.validation.isValid = false;
            data.validation.errors.push('Agent objective is required');
        }
        if (!data.connection.id) {
            data.validation.isValid = false;
            data.validation.errors.push('Database connection is required');
        }
        
        // === AVAILABLE ACTIONS ===
        if (data.mode === 'create') {
            data.availableActions = [
                'Enter agent name',
                'Write agent objective',
                'Select database connection',
                'Save new agent',
                'Cancel creation'
            ];
        } else {
            data.availableActions = [
                'Select different agent',
                'Modify agent objective',
                'Change database connection',
                'Save changes',
                'Delete agent',
                'Create new agent'
            ];
        }
        
        // Debug summary
        console.log('=== Data Agent Context ===');
        console.log('Mode:', data.mode);
        console.log('Agent:', data.agent.name || '(unnamed)', '| ID:', data.agent.id);
        console.log('Objective length:', data.agent.objective.length, 'chars');
        console.log('Connection:', data.connection.name || '(none selected)');
        console.log('Available agents:', data.agentsList.total);
        console.log('Available connections:', data.connectionsList.total);
        console.log('Valid:', data.validation.isValid, data.validation.errors.length ? '- Errors: ' + data.validation.errors.join(', ') : '');
        
        return data;
    }
};

console.log('Data Agent Builder context loaded');
