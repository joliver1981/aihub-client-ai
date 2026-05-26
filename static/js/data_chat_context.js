/**
 * Page context provider for /data_chat (templates/data_chat.html).
 * Mirrors the message-content capture from chat_context.js so the assistant
 * can answer questions about what the data agent just said or returned.
 */
window.assistantPageContext = {
    page: 'data_chat',
    pageName: 'Data Assistant Chat',

    getPageData: function () {
        const data = {
            agent: {
                id: null,
                name: 'None selected',
                objective: ''
            },
            availableAgents: [],
            cautionLevel: null,
            conversation: {
                userMessageCount: 0,
                agentMessageCount: 0,
                totalMessageCount: 0,
                hasActiveConversation: false,
                hasReachedMaxLength: false,
                lastUserMessage: null,
                lastAgentMessage: null,
                recentMessages: []
            },
            input: {
                draft: '',
                hasDraft: false,
                draftLength: 0
            },
            explain: {
                visible: false,
                hasContent: false
            },
            ui: {
                theme: 'dark',
                welcomeStateVisible: false,
                detailPanelOpen: false,
                errorModalOpen: false,
                errorText: ''
            },
            availableActions: []
        };

        const agentDropdown = document.getElementById('agent-dropdown');
        const agentIdInput = document.getElementById('agent_id');
        if (agentIdInput && agentIdInput.value) data.agent.id = agentIdInput.value;
        if (agentDropdown) {
            const selected = agentDropdown.options[agentDropdown.selectedIndex];
            if (selected && selected.value) {
                data.agent.id = data.agent.id || selected.value;
                data.agent.name = (selected.textContent || '').trim() || data.agent.name;
            }
            Array.from(agentDropdown.options).forEach(function (opt) {
                const v = (opt.value || '').trim();
                const t = (opt.textContent || '').trim();
                if (v && t) data.availableAgents.push({ id: v, name: t });
            });
        }
        const objective = document.getElementById('objective');
        if (objective) data.agent.objective = (objective.value || '').trim();

        const cautionSel = document.getElementById('caution-level-setting');
        if (cautionSel) {
            data.cautionLevel = cautionSel.value || null;
        }

        // Conversation message capture (same approach as chat_context.js)
        const MAX_MESSAGES = 12;
        const MAX_CHARS_PER_MSG = 600;
        const bubbles = document.querySelectorAll('#chat-content .msg-bubble');
        bubbles.forEach(function (bubble) {
            const isUser = bubble.classList.contains('user-bubble');
            const isAgent = bubble.classList.contains('ai-bubble');
            if (!isUser && !isAgent) return;
            let text = (bubble.innerText || bubble.textContent || '').trim();
            if (!text) return;
            if (text.length > MAX_CHARS_PER_MSG) text = text.slice(0, MAX_CHARS_PER_MSG) + '…';
            const role = isUser ? 'user' : 'agent';
            data.conversation.recentMessages.push({ role: role, text: text });
            if (isUser) {
                data.conversation.userMessageCount += 1;
                data.conversation.lastUserMessage = text;
            } else {
                data.conversation.agentMessageCount += 1;
                data.conversation.lastAgentMessage = text;
            }
        });
        if (data.conversation.recentMessages.length > MAX_MESSAGES) {
            data.conversation.recentMessages =
                data.conversation.recentMessages.slice(-MAX_MESSAGES);
        }
        data.conversation.totalMessageCount =
            data.conversation.userMessageCount + data.conversation.agentMessageCount;
        data.conversation.hasActiveConversation = data.conversation.totalMessageCount > 0;

        const maxBanner = document.getElementById('conversation-max-banner');
        if (maxBanner) {
            data.conversation.hasReachedMaxLength =
                maxBanner.style.display !== 'none' && maxBanner.offsetParent !== null;
        }

        const input = document.getElementById('user-input');
        if (input) {
            const draft = (input.value || '').trim();
            data.input.draft = draft.length > 200 ? draft.slice(0, 200) + '…' : draft;
            data.input.draftLength = draft.length;
            data.input.hasDraft = draft.length > 0;
        }

        const explainContainer = document.getElementById('explain-container');
        if (explainContainer && explainContainer.style.display !== 'none') {
            data.explain.visible = true;
            const explainContent = document.getElementById('explain-content');
            if (explainContent && explainContent.style.display !== 'none' &&
                (explainContent.textContent || '').trim().length > 0) {
                data.explain.hasContent = true;
            }
        }

        data.ui.theme = document.body.classList.contains('light-mode') ? 'light' : 'dark';
        const welcome = document.getElementById('welcome-state');
        if (welcome) data.ui.welcomeStateVisible = welcome.offsetParent !== null;
        const detailPanel = document.getElementById('chat-panel');
        if (detailPanel) {
            data.ui.detailPanelOpen = detailPanel.classList.contains('open') ||
                detailPanel.classList.contains('visible');
        }
        const errorModal = document.getElementById('errorModal');
        if (errorModal && errorModal.classList.contains('show')) {
            data.ui.errorModalOpen = true;
            const errorText = document.getElementById('errorModalText');
            if (errorText) data.ui.errorText = (errorText.textContent || '').trim();
        }

        if (!data.agent.id) {
            data.availableActions.push('Pick a data agent from the dropdown');
        } else {
            data.availableActions.push('Ask a question about your data in plain English');
            if (data.conversation.hasActiveConversation) {
                data.availableActions.push('Click "Want me to explain?" to see how the agent answered');
                data.availableActions.push('Reset the conversation to start fresh');
            }
            if (data.conversation.hasReachedMaxLength) {
                data.availableActions.push('Maximum conversation length reached — Reset to continue');
            }
        }

        return data;
    }
};

console.log('Data Chat assistant context loaded');
