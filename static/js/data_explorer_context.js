/**
 * Page context provider for /data_explorer (templates/data_explorer.html).
 * Data Explorer is a conversational data-analysis + dashboarding surface.
 */
window.assistantPageContext = {
    page: 'data_explorer',
    pageName: 'Data Explorer',

    getPageData: function () {
        const data = {
            agent: {
                id: null,
                name: 'None selected',
                objective: ''
            },
            availableAgents: [],
            dashboards: {
                savedCount: 0,
                items: [],
                panelOpen: false,
                currentTitle: '',
                tileCount: 0,
                hasUnsavedChanges: false
            },
            conversation: {
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
            status: {
                visible: false,
                label: ''
            },
            ui: {
                theme: 'dark',
                welcomeStateVisible: false,
                detailPanelOpen: false,
                saveModalOpen: false
            },
            availableActions: []
        };

        const agentDropdown = document.getElementById('agentDropdown');
        if (agentDropdown) {
            const selected = agentDropdown.options[agentDropdown.selectedIndex];
            if (selected && selected.value) {
                data.agent.id = selected.value;
                data.agent.name = (selected.textContent || '').trim() || data.agent.name;
            }
            Array.from(agentDropdown.options).forEach(function (opt) {
                const v = (opt.value || '').trim();
                const t = (opt.textContent || '').trim();
                if (v && t && v !== '') data.availableAgents.push({ id: v, name: t });
            });
        }
        const objective = document.getElementById('agentObjective');
        if (objective) data.agent.objective = (objective.textContent || '').trim();

        const savedList = document.getElementById('savedDashboardsList');
        if (savedList) {
            const empty = savedList.querySelector('.de-saved-empty');
            if (!empty || empty.offsetParent === null) {
                savedList.querySelectorAll('.de-saved-item, a, button, li').forEach(function (el) {
                    if (el.offsetParent === null) return;
                    const txt = (el.textContent || '').trim().split('\n')[0];
                    if (txt && txt.length < 120) data.dashboards.items.push(txt);
                });
                data.dashboards.savedCount = data.dashboards.items.length;
            }
        }

        const dashPanel = document.getElementById('dashPanel');
        if (dashPanel) {
            data.dashboards.panelOpen = dashPanel.classList.contains('open') ||
                dashPanel.classList.contains('visible') ||
                getComputedStyle(dashPanel).transform.indexOf('matrix') === 0;
        }
        const dashTitleText = document.getElementById('dashboardTitleText');
        if (dashTitleText) data.dashboards.currentTitle = (dashTitleText.textContent || '').trim();
        const gridStack = document.getElementById('dashboardGrid');
        if (gridStack) {
            data.dashboards.tileCount = gridStack.querySelectorAll('.grid-stack-item').length;
        }

        // Conversation capture — Data Explorer uses its own .de-message class set.
        // We try a few common shapes to stay resilient if the markup evolves.
        const MAX_MESSAGES = 12;
        const MAX_CHARS_PER_MSG = 600;
        const messageContainer = document.getElementById('chatMessages');
        if (messageContainer) {
            const candidates = messageContainer.querySelectorAll(
                '.de-message, .de-user-message, .de-agent-message, .message, .msg-bubble'
            );
            candidates.forEach(function (el) {
                let role = null;
                if (el.classList.contains('de-user-message') ||
                    el.classList.contains('user-bubble') ||
                    el.classList.contains('user')) {
                    role = 'user';
                } else if (el.classList.contains('de-agent-message') ||
                           el.classList.contains('ai-bubble') ||
                           el.classList.contains('assistant') ||
                           el.classList.contains('agent')) {
                    role = 'agent';
                } else if (el.classList.contains('de-message')) {
                    // Walk into the bubble to decide
                    if (el.querySelector('.user-bubble, .de-user-bubble')) role = 'user';
                    else if (el.querySelector('.ai-bubble, .de-agent-bubble')) role = 'agent';
                }
                if (!role) return;
                let text = (el.innerText || el.textContent || '').trim();
                if (!text) return;
                if (text.length > MAX_CHARS_PER_MSG) text = text.slice(0, MAX_CHARS_PER_MSG) + '…';
                data.conversation.recentMessages.push({ role: role, text: text });
                if (role === 'user') {
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
        }

        const input = document.getElementById('userInput');
        if (input) {
            const draft = (input.value || '').trim();
            data.input.draft = draft.length > 200 ? draft.slice(0, 200) + '…' : draft;
            data.input.draftLength = draft.length;
            data.input.hasDraft = draft.length > 0;
        }

        const statusIndicator = document.getElementById('statusIndicator');
        if (statusIndicator && statusIndicator.style.display !== 'none') {
            data.status.visible = true;
            const label = document.getElementById('statusLabel');
            if (label) data.status.label = (label.textContent || '').trim();
        }

        const page = document.getElementById('explorerPage');
        if (page) data.ui.theme = page.classList.contains('light-mode') ? 'light' : 'dark';
        const welcome = document.getElementById('welcomeState');
        if (welcome) data.ui.welcomeStateVisible = welcome.offsetParent !== null;
        const detail = document.getElementById('detailPanel');
        if (detail) {
            data.ui.detailPanelOpen = detail.classList.contains('open') ||
                detail.classList.contains('visible');
        }
        const saveModal = document.getElementById('saveDashboardModal');
        if (saveModal && saveModal.style.display !== 'none') data.ui.saveModalOpen = true;

        if (!data.agent.id) {
            data.availableActions.push('Pick a data source from the Data Source dropdown');
        } else {
            data.availableActions.push('Ask a question about your data — e.g. "show me sales by month"');
            data.availableActions.push('Click any chip suggestion to send a starter question');
            if (data.conversation.hasActiveConversation) {
                data.availableActions.push('Pin a chart to a dashboard, or open Dashboards in the sidebar');
            }
            if (data.dashboards.tileCount > 0) {
                data.availableActions.push('Save the current dashboard with the Save button');
            }
        }

        return data;
    }
};

console.log('Data Explorer assistant context loaded');
