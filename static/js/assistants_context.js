
window.assistantPageContext = {
    page: 'assistants',
    pageName: 'AI Assistants',
    
    getPageData: function() {
        const data = {
            // Selected agent
            agent: {
                id: null,
                name: 'None selected',
                description: ''
            },
            
            // Available agents
            availableAgents: [],
            
            // Chat state
            chat: {
                messageCount: 0,
                userMessageCount: 0,
                agentMessageCount: 0,
                hasActiveConversation: false,
                lastMessageTime: null
            },
            
            // Settings
            settings: {
                temperature: 0.7,
                maxTokens: 2000,
                showToolCalls: true
            },
            
            // Session info
            session: {
                id: null,
                duration: 0
            },
            
            // Input state
            input: {
                hasText: false,
                textLength: 0
            },
            
            // Agent capabilities (if known)
            capabilities: [],
            
            // Available actions
            availableActions: []
        };
        
        // Get selected agent
        const agentSelect = $('#agent-select, #agentSelect, select[name="agent"]');
        if (agentSelect.length && agentSelect.val()) {
            data.agent.id = agentSelect.val();
            data.agent.name = agentSelect.find('option:selected').text().trim();
        }
        
        // Get available agents
        agentSelect.find('option').each(function() {
            const val = $(this).val();
            const text = $(this).text().trim();
            if (val && text && val !== '') {
                data.availableAgents.push({
                    id: val,
                    name: text
                });
            }
        });
        
        // Count messages
        const chatContainer = $('#chatBox, #chat-messages, .optimized-chat-box, .chat-container');
        const userMessages = chatContainer.find('.user-message, .user-bubble, .message-user');
        const agentMessages = chatContainer.find('.agent-message, .agent-bubble, .message-assistant');
        
        data.chat.userMessageCount = userMessages.length;
        data.chat.agentMessageCount = agentMessages.length;
        data.chat.messageCount = data.chat.userMessageCount + data.chat.agentMessageCount;
        data.chat.hasActiveConversation = data.chat.messageCount > 0;
        
        // Get settings if visible
        const tempSlider = $('#temperature, input[name="temperature"]');
        if (tempSlider.length) {
            data.settings.temperature = parseFloat(tempSlider.val()) || 0.7;
        }
        
        const maxTokensInput = $('#maxTokens, input[name="max_tokens"]');
        if (maxTokensInput.length) {
            data.settings.maxTokens = parseInt(maxTokensInput.val()) || 2000;
        }
        
        const showToolsCheckbox = $('#showToolCalls, input[name="show_tools"]');
        if (showToolsCheckbox.length) {
            data.settings.showToolCalls = showToolsCheckbox.is(':checked');
        }
        
        // Check input state
        const inputField = $('#userInput, #message-input, textarea[name="message"]');
        if (inputField.length) {
            const text = inputField.val() || '';
            data.input.hasText = text.trim().length > 0;
            data.input.textLength = text.length;
        }
        
        // Try to get agent capabilities from displayed tools
        $('.agent-tool, .tool-badge, .capability-item').each(function() {
            const toolName = $(this).text().trim();
            if (toolName) {
                data.capabilities.push(toolName);
            }
        });
        
        // Determine available actions
        if (!data.agent.id) {
            data.availableActions.push('Select an agent from the dropdown');
        } else {
            data.availableActions.push('Type a message to chat');
            data.availableActions.push('Ask questions about your data');
            data.availableActions.push('Request reports or summaries');
            
            if (data.chat.hasActiveConversation) {
                data.availableActions.push('Continue the conversation');
                data.availableActions.push('Clear chat to start fresh');
                data.availableActions.push('Export conversation');
            }
            
            data.availableActions.push('Adjust settings (temperature, etc.)');
        }
        
        return data;
    }
};

console.log('Assistants assistant context loaded');
