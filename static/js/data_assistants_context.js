/**
 * Page Context: Data Assistants (Data Agent Chat)
 * 
 * Provides real-time context about the data agent chat page state.
 * Add this script to data_assistants.html
 * 
 * Extracts:
 * - Selected agent and objective
 * - Chat history summary
 * - Caution level setting
 * - Current input
 * - Loading/response states
 */

window.assistantPageContext = {
    page: 'data_assistants',
    pageName: 'Data Agent Chat',
    
    getPageData: function() {
        const data = {
            // Selected agent
            selectedAgent: {
                id: null,
                name: '',
                objective: '',
                isSelected: false
            },
            
            // Available agents
            agents: {
                total: 0,
                list: []
            },
            
            // Chat state
            chat: {
                messageCount: 0,
                hasMessages: false,
                lastQuestion: '',
                isLoading: false,
                reachedLimit: false
            },
            
            // Current input
            currentInput: '',
            
            // Caution level
            cautionLevel: {
                value: 'medium',
                label: 'Medium - Balanced approach'
            },
            
            // UI state
            uiState: {
                explainVisible: false,
                errorModalVisible: false
            },
            
            // Available actions
            availableActions: [],
            
            // Validation
            validation: {
                canSendMessage: false,
                errors: []
            }
        };
        
        // === SELECTED AGENT ===
        const agentDropdown = document.getElementById('agent-dropdown');
        if (agentDropdown) {
            // Get all agents from dropdown
            for (let i = 0; i < agentDropdown.options.length; i++) {
                const option = agentDropdown.options[i];
                if (option.value) {
                    data.agents.list.push({
                        id: option.value,
                        name: option.text.trim()
                    });
                }
            }
            data.agents.total = data.agents.list.length;
            
            // Get selected agent
            if (agentDropdown.value) {
                data.selectedAgent.id = agentDropdown.value;
                const selectedOption = agentDropdown.options[agentDropdown.selectedIndex];
                data.selectedAgent.name = selectedOption ? selectedOption.text.trim() : '';
                data.selectedAgent.isSelected = true;
            }
        }
        
        // Get agent objective
        const objectiveField = document.getElementById('objective');
        if (objectiveField && objectiveField.value) {
            data.selectedAgent.objective = objectiveField.value.trim();
        }
        
        // Hidden agent_id field
        const agentIdField = document.getElementById('agent_id');
        if (agentIdField && agentIdField.value) {
            data.selectedAgent.id = agentIdField.value;
        }
        
        // === CHAT STATE ===
        const chatContent = document.getElementById('chat-content');
        if (chatContent) {
            // Count message bubbles
            const userMessages = chatContent.querySelectorAll('.user-bubble');
            const agentMessages = chatContent.querySelectorAll('.agent-bubble');
            data.chat.messageCount = userMessages.length + agentMessages.length;
            data.chat.hasMessages = data.chat.messageCount > 0;
            
            // Get last user question
            if (userMessages.length > 0) {
                const lastUserMsg = userMessages[userMessages.length - 1];
                data.chat.lastQuestion = lastUserMsg.textContent.trim().substring(0, 100) +
                    (lastUserMsg.textContent.length > 100 ? '...' : '');
            }
        }
        
        // Check loading state
        const loadingSpinner = document.getElementById('loading-spinner');
        if (loadingSpinner) {
            data.chat.isLoading = loadingSpinner.style.display !== 'none';
        }
        
        // Check conversation limit banner
        const limitBanner = document.getElementById('conversation-max-banner');
        if (limitBanner) {
            data.chat.reachedLimit = limitBanner.style.display !== 'none';
        }
        
        // === CURRENT INPUT ===
        const userInput = document.getElementById('user-input');
        if (userInput) {
            data.currentInput = userInput.value.trim();
        }
        
        // === CAUTION LEVEL ===
        const cautionSelect = document.getElementById('caution-level-setting');
        if (cautionSelect) {
            data.cautionLevel.value = cautionSelect.value;
            const selectedOption = cautionSelect.options[cautionSelect.selectedIndex];
            data.cautionLevel.label = selectedOption ? selectedOption.text.trim() : '';
        }
        
        // === UI STATE ===
        const explainContainer = document.getElementById('explain-container');
        if (explainContainer) {
            data.uiState.explainVisible = explainContainer.style.display !== 'none';
        }
        
        const errorModal = document.getElementById('errorModal');
        if (errorModal && errorModal.classList.contains('show')) {
            data.uiState.errorModalVisible = true;
        }
        
        // === VALIDATION ===
        if (!data.selectedAgent.isSelected) {
            data.validation.errors.push('No agent selected');
        }
        if (data.chat.reachedLimit) {
            data.validation.errors.push('Conversation limit reached - reset required');
        }
        
        data.validation.canSendMessage = data.selectedAgent.isSelected && 
                                          !data.chat.isLoading && 
                                          !data.chat.reachedLimit;
        
        // === AVAILABLE ACTIONS ===
        if (!data.selectedAgent.isSelected) {
            data.availableActions = [
                'Select a Data Agent from the dropdown',
                'View agent objective to understand capabilities'
            ];
        } else if (data.chat.reachedLimit) {
            data.availableActions = [
                'Click Reset to start a new conversation',
                'Previous messages will be cleared'
            ];
        } else if (data.chat.isLoading) {
            data.availableActions = [
                'Waiting for agent response...',
                'Please wait for the current query to complete'
            ];
        } else if (!data.chat.hasMessages) {
            data.availableActions = [
                'Type a question about your data',
                'Ask in natural language',
                'Press Enter or click Send'
            ];
        } else {
            data.availableActions = [
                'Ask another question',
                'Ask a follow-up question',
                'Request explanation of last response',
                'Provide feedback on response',
                'Reset conversation to start fresh'
            ];
            
            if (data.uiState.explainVisible) {
                data.availableActions.unshift('Click "Want me to explain?" for more details');
            }
        }
        
        // Debug summary
        console.log('=== Data Assistants Context ===');
        console.log('Selected agent:', data.selectedAgent.name || '(none)');
        console.log('Agent objective:', data.selectedAgent.objective ? 
                   data.selectedAgent.objective.substring(0, 50) + '...' : '(none)');
        console.log('Messages:', data.chat.messageCount);
        console.log('Loading:', data.chat.isLoading);
        console.log('Caution level:', data.cautionLevel.value);
        console.log('Can send:', data.validation.canSendMessage);
        if (data.chat.lastQuestion) {
            console.log('Last question:', data.chat.lastQuestion);
        }
        
        return data;
    }
};

console.log('Data Assistants context loaded');
