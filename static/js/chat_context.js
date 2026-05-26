/**
 * Page context provider for the /chat route (templates/chat.html).
 *
 * Exposes window.assistantPageContext for the Universal Assistant widget.
 * The `page` field must match a folder under assistant_docs/pages/ so the
 * backend DocumentationManager can load the matching guide(s).
 */
window.assistantPageContext = {
    page: 'chat',
    pageName: 'Agent Chat',

    getPageData: function () {
        const data = {
            agent: {
                id: null,
                name: 'None selected',
                objective: ''
            },
            availableAgents: [],
            conversation: {
                id: null,
                userMessageCount: 0,
                agentMessageCount: 0,
                totalMessageCount: 0,
                hasActiveConversation: false,
                lastUserMessage: null,
                lastAgentMessage: null,
                recentMessages: []
            },
            input: {
                draft: '',
                hasDraft: false,
                draftLength: 0
            },
            documentContext: {
                visible: false,
                queuedFiles: 0,
                uploadedDocumentCount: 0,
                uploadedDocuments: []
            },
            mcpServers: {
                visible: false,
                servers: []
            },
            ui: {
                theme: 'dark',
                historySidebarOpen: false,
                detailPanelOpen: false,
                welcomeStateVisible: false
            },
            availableActions: []
        };

        // -----------------------------------------------------------------
        // Selected agent
        // -----------------------------------------------------------------
        const agentDropdown = document.getElementById('agent-dropdown');
        const agentIdInput = document.getElementById('agent_id');

        if (agentIdInput && agentIdInput.value) {
            data.agent.id = agentIdInput.value;
        }
        if (agentDropdown) {
            const selected = agentDropdown.options[agentDropdown.selectedIndex];
            if (selected && selected.value) {
                data.agent.id = data.agent.id || selected.value;
                data.agent.name = (selected.textContent || '').trim() || data.agent.name;
            }
            Array.from(agentDropdown.options).forEach(function (opt) {
                const val = (opt.value || '').trim();
                const txt = (opt.textContent || '').trim();
                if (val && txt) {
                    data.availableAgents.push({ id: val, name: txt });
                }
            });
        }

        // Header sometimes shows the actively-chatting agent before the
        // dropdown rebinds (e.g. when resumed from history).
        const headerName = document.getElementById('headerAgentName');
        if (headerName && data.agent.name === 'None selected') {
            const txt = (headerName.textContent || '').trim();
            if (txt && txt !== 'Agent Chat') {
                data.agent.name = txt;
            }
        }

        const objectiveEl = document.getElementById('objective');
        if (objectiveEl) {
            data.agent.objective = (objectiveEl.value || '').trim();
        }

        // -----------------------------------------------------------------
        // Conversation messages — capture actual content, not just counts.
        // This is the gap that caused "I can't see the agent's last reply".
        // -----------------------------------------------------------------
        const MAX_MESSAGES = 12;        // keep most recent N exchanges
        const MAX_CHARS_PER_MSG = 600;  // truncate long bodies

        const bubbles = document.querySelectorAll('#chat-content .msg-bubble');
        bubbles.forEach(function (bubble) {
            const isUser = bubble.classList.contains('user-bubble');
            const isAgent = bubble.classList.contains('ai-bubble');
            if (!isUser && !isAgent) return;

            let text = (bubble.innerText || bubble.textContent || '').trim();
            if (!text) return;
            if (text.length > MAX_CHARS_PER_MSG) {
                text = text.slice(0, MAX_CHARS_PER_MSG) + '…';
            }

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
        data.conversation.hasActiveConversation =
            data.conversation.totalMessageCount > 0;

        if (typeof window.currentConversationId !== 'undefined') {
            data.conversation.id = window.currentConversationId;
        }

        // -----------------------------------------------------------------
        // Input draft
        // -----------------------------------------------------------------
        const input = document.getElementById('user-input');
        if (input) {
            const draft = (input.value || '').trim();
            data.input.draft = draft.length > 200 ? draft.slice(0, 200) + '…' : draft;
            data.input.draftLength = draft.length;
            data.input.hasDraft = draft.length > 0;
        }

        // -----------------------------------------------------------------
        // Document context panel
        // -----------------------------------------------------------------
        const docCard = document.getElementById('documentUploadCard');
        if (docCard) {
            data.documentContext.visible = docCard.style.display !== 'none';
        }
        const queueCount = document.getElementById('queueCount');
        if (queueCount) {
            data.documentContext.queuedFiles = parseInt(queueCount.textContent, 10) || 0;
        }
        if (Array.isArray(window.existingDocuments)) {
            data.documentContext.uploadedDocumentCount = window.existingDocuments.length;
            data.documentContext.uploadedDocuments = window.existingDocuments
                .slice(0, 10)
                .map(function (doc) {
                    return {
                        name: doc.filename || doc.name || doc.title || 'unnamed',
                        type: doc.type || doc.content_type || null
                    };
                });
        }

        // -----------------------------------------------------------------
        // MCP servers panel
        // -----------------------------------------------------------------
        const mcpCard = document.getElementById('mcpServersCard');
        if (mcpCard) {
            data.mcpServers.visible = mcpCard.style.display !== 'none';
            const items = mcpCard.querySelectorAll('#mcpServersList .mcp-server, #mcpServersList li, #mcpServersList .server-item');
            items.forEach(function (el) {
                const name = (el.textContent || '').trim().split('\n')[0];
                if (name) data.mcpServers.servers.push(name);
            });
        }

        // -----------------------------------------------------------------
        // UI state
        // -----------------------------------------------------------------
        data.ui.theme = document.body.classList.contains('light-mode') ? 'light' : 'dark';
        const historySidebar = document.getElementById('history-sidebar');
        if (historySidebar) {
            data.ui.historySidebarOpen = historySidebar.style.display !== 'none';
        }
        const detailPanel = document.getElementById('chat-panel');
        if (detailPanel) {
            data.ui.detailPanelOpen = detailPanel.classList.contains('open') ||
                detailPanel.classList.contains('visible');
        }
        const welcome = document.getElementById('welcome-state');
        if (welcome) {
            data.ui.welcomeStateVisible = welcome.offsetParent !== null;
        }

        // -----------------------------------------------------------------
        // Available actions hint (helps the assistant suggest next steps)
        // -----------------------------------------------------------------
        if (!data.agent.id) {
            data.availableActions.push('Select an agent from the Agent Selection dropdown');
        } else {
            data.availableActions.push('Type a message and press Enter to send');
            if (data.documentContext.visible) {
                data.availableActions.push('Attach files via the Document Context panel');
            }
            if (data.conversation.hasActiveConversation) {
                data.availableActions.push('Open Conversation History (clock icon) to revisit past chats');
                data.availableActions.push('Reset conversation (sync icon) to start fresh');
            }
        }

        return data;
    }
};

console.log('Chat page assistant context loaded');
