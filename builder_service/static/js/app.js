/**
 * AI Hub Builder — Main Application
 * 
 * Orchestrates: User input → Processing pipeline → Streaming response → Plan cards → Sidebar
 */

import { ApiClient } from './api.js?v=4';
import { ChatManager } from './chat.js?v=8';
import { InputBar } from './input-bar.js?v=4';
import { Sidebar } from './sidebar.js?v=4';
import { ThemeManager } from './theme.js?v=4';


class BuilderApp {
    constructor() {
        this.currentSessionId = null;
        this.sessions = [];
        this.currentPlan = null;
        this.userContext = null;  // User info from Flask app
        this.authToken = null;    // Token from URL for auth
        this._processingNewChat = false;

        this.api = new ApiClient();
        this.chat = new ChatManager(document.getElementById('chat-container'));
        this.theme = new ThemeManager();

        this.inputBar = new InputBar(
            document.getElementById('input-bar'),
            {
                onSend: (text, attachments) => this._handleSend(text, attachments),
                onUpload: (files) => this.api.uploadFiles(files, this.currentSessionId),
                onDeleteFile: (fileId) => this.api.deleteUpload(fileId),
            },
        );

        this.sidebar = new Sidebar({
            sessionList: document.getElementById('session-list'),
            rightSidebar: document.getElementById('sidebar-right'),
            onSessionSelect: (id) => this._handleSessionSelect(id),
            onNewChat: () => {
                if (this._processingNewChat) return;
                this._processingNewChat = true;
                this._handleNewChat().finally(() => { this._processingNewChat = false; });
            },
            onSessionDelete: (id) => this._handleSessionDelete(id),
        });

        // Quick action chips
        document.querySelectorAll('.quick-action').forEach(btn => {
            btn.addEventListener('click', () => {
                const prompt = btn.dataset.prompt;
                if (prompt) this._handleSend(prompt);
            });
        });

        this._init();
    }

    async _init() {
        // Check for authentication token in URL
        await this._handleAuthentication();

        await this._refreshSessions();
        this.inputBar.focus();
        this._updateTitle('New Chat');
    }

    // ─── Authentication ──────────────────────────────────────

    async _handleAuthentication() {
        const urlParams = new URLSearchParams(window.location.search);
        const token = urlParams.get('token');

        if (token) {
            console.log('Found authentication token, validating...');
            try {
                const result = await this.api.validateToken(token);

                if (result.valid && result.user) {
                    this.userContext = result.user;
                    this.authToken = token;
                    console.log(`Authenticated as: ${result.user.name} (${result.user.username})`);

                    // Update UI to show user is authenticated
                    this._updateUserDisplay();

                    // Clean up URL (remove token from address bar for security)
                    const cleanUrl = window.location.pathname;
                    window.history.replaceState({}, document.title, cleanUrl);
                    return; // Successfully authenticated via URL token
                } else {
                    console.warn('Token validation failed:', result.error);
                }
            } catch (err) {
                console.error('Error validating token:', err);
            }
        }

        // No token in URL — try auto-authenticating from the main app session
        await this._tryAutoAuthenticate();
    }

    async _tryAutoAuthenticate() {
        try {
            // Get the main app URL from the builder service config
            const configRes = await fetch('/api/auth/config');
            if (!configRes.ok) return;
            const config = await configRes.json();
            const mainAppUrl = config.main_app_url;
            if (!mainAppUrl) return;

            // Request a builder token using the main app's session cookie
            const tokenRes = await fetch(`${mainAppUrl}/api/builder-auto-token`, {
                credentials: 'include',
            });

            if (!tokenRes.ok) {
                console.log('Auto-auth: not logged in to main app (or CORS blocked)');
                return;
            }

            const { token } = await tokenRes.json();
            if (!token) return;

            // Validate the token through the builder service (same as URL token flow)
            const result = await this.api.validateToken(token);
            if (result.valid && result.user) {
                this.userContext = result.user;
                this.authToken = token;
                console.log(`Auto-authenticated as: ${result.user.name} (${result.user.username})`);
                this._updateUserDisplay();
            }
        } catch (err) {
            // Silently continue as anonymous — user may not be logged in
            console.log('Auto-auth: could not authenticate from main app session');
        }
    }

    _updateUserDisplay() {
        // Update the connection status to show authenticated user
        const statusEl = document.getElementById('agent-status');
        if (statusEl && this.userContext) {
            statusEl.innerHTML = `
                <div class="w-1.5 h-1.5 rounded-full bg-emerald-400"></div>
                <span class="text-xs text-zinc-500 font-mono">${this.userContext.name || this.userContext.username}</span>
            `;
        }
    }

    // ─── Message Flow ───────────────────────────────────────

    async _handleSend(text, attachments = []) {
        if (!text.trim() && attachments.length === 0) return;

        if (!this.currentSessionId) {
            await this._createSession();
        }

        // Render user message with attachment indicators
        const attachmentNames = attachments.map(a => a.filename);
        this.chat.addUserMessage(text, attachmentNames);

        // Disable input & show processing pipeline
        this.inputBar.disable();
        this.chat.showProcessing();

        // Extract file IDs for the API call
        const fileIds = attachments.map(a => a.file_id);

        let stream = null;
        let receivedTokens = false;

        try {
            for await (const { event, data } of this.api.streamChat(this.currentSessionId, text, fileIds.length > 0 ? fileIds : null)) {
                switch (event) {
                    case 'status':
                        // Don't create processing pipeline if already streaming tokens
                        // (happens during auto-execute when execute node starts mid-stream)
                        if (!receivedTokens) {
                            this.chat.updateProcessingStep(
                                data.phase || 'processing',
                                data.label || 'Processing...',
                                data.icon || 'brain',
                            );
                        }
                        this.chat.updateStatus(data.phase || 'processing');
                        break;

                    case 'token':
                        // First token — swap processing pipeline for AI bubble
                        if (!receivedTokens) {
                            receivedTokens = true;
                            stream = this.chat.startAIMessage();
                        }
                        stream.appendToken(data.text || '');
                        break;

                    case 'plan':
                        // Structured plan received — show card in chat + right sidebar
                        if (stream) stream.complete();

                        console.log('[plan] Received:', {
                            status: data.status,
                            goal: data.goal?.substring(0, 50),
                            steps: data.steps?.map(s => ({ order: s.order, status: s.status })),
                        });

                        // Check if this is an update to an existing plan or a new one
                        const isUpdate = this.currentPlan &&
                            this.currentPlan.goal === data.goal &&
                            data.status !== 'draft';

                        this.currentPlan = data;

                        if (isUpdate) {
                            // Update existing plan card and sidebar
                            this._updatePlanCard(data);
                        } else if (data.status === 'draft') {
                            // New plan - render fresh card
                            this._renderPlanCard(data);
                        } else {
                            // Auto-executed plan (arrived already completed) - render as completed card
                            this._renderPlanCard(data);
                        }

                        this._renderSidebarPlan(data);
                        // Don't start a new stream — plan card is the final output
                        stream = null;
                        break;

                    case 'plan_update':
                        // Execution completed — update sidebar and plan card
                        console.log('[plan_update] Received:', {
                            status: data.status,
                            steps: data.steps?.map(s => ({ order: s.order, status: s.status })),
                        });
                        this.currentPlan = data;
                        this._updatePlanCard(data);
                        this._renderSidebarPlan(data);
                        break;

                    case 'agent_conversation':
                        // Agent conversation state update
                        console.log('[agent_conversation] Received update:', {
                            conversation_id: data.conversation_id,
                            agent_name: data.agent_name,
                            status: data.status,
                            message_count: data.message_count,
                            messages_received: data.messages?.length || 0,
                            is_current: data.is_current,
                        });
                        this._updateAgentConversation(data);
                        break;

                    case 'agent_question':
                        // Agent is asking a question - show it prominently
                        console.log('Agent question:', data);
                        this._showAgentQuestion(data);
                        break;

                    case 'error':
                        this.chat.removeProcessing();
                        this.chat.addErrorMessage(data.message || 'An error occurred');
                        break;

                    case 'done':
                        if (stream) stream.complete();
                        this.chat.removeProcessing();
                        this.chat.updateStatus('ready');
                        if (data.session_id) this.currentSessionId = data.session_id;
                        break;
                }
            }
        } catch (err) {
            console.error('Chat error:', err);
            this.chat.removeProcessing();
            if (err.name !== 'AbortError') {
                this.chat.addErrorMessage(
                    err.message.includes('Failed to fetch')
                        ? 'Could not connect to the builder service. Is it running?'
                        : err.message
                );
            }
        } finally {
            this.inputBar.enable();
            this.chat.updateStatus('ready');
            await this._refreshSessions();
        }
    }

    // ─── Plan Rendering ─────────────────────────────────────

    _renderPlanCard(plan) {
        // Store reference to the plan card for updates
        this._currentPlanCard = this.chat.addPlanCard(
            plan,
            () => this._handleSend('Yes, go ahead and execute the plan.'),
            () => {
                this.inputBar.setValue("I'd like to change the plan: ");
                this.inputBar.focus();
            },
        );
    }

    _updatePlanCard(plan) {
        // Find and update the existing plan card in the chat
        const planCards = document.querySelectorAll('.plan-card-wrapper .plan-card');
        if (planCards.length === 0) return;

        const card = planCards[planCards.length - 1]; // Most recent plan card

        // Update step statuses
        const stepEls = card.querySelectorAll('.plan-step');
        plan.steps.forEach((step, i) => {
            if (stepEls[i]) {
                const dot = stepEls[i].querySelector('.plan-step-dot');
                if (dot) {
                    // Map step statuses to CSS classes
                    const statusClass = this._mapStepStatusToClass(step.status);
                    dot.className = `plan-step-dot ${statusClass}`;
                }
            }
        });

        // Hide action buttons if plan is no longer a draft
        if (plan.status !== 'draft') {
            const actions = card.querySelector('.plan-card-actions');
            if (actions) {
                actions.style.display = 'none';
            }
        }
    }

    _mapStepStatusToClass(status) {
        // Map various status values to CSS classes
        const statusMap = {
            'pending': '',
            'running': 'running',
            'completed': 'completed',
            'failed': 'failed',
            'delegated': 'delegated',
            'awaiting_input': 'awaiting',
            'skipped': 'skipped',
        };
        return statusMap[status] || '';
    }

    _getStepVisualStyle(status) {
        // Returns visual styling for sidebar steps based on status
        const styles = {
            'completed': {
                stepClass: 'completed',
                numberStyle: 'background:rgba(52,211,153,0.2);color:#34d399;',
                statusIcon: '✓',
            },
            'running': {
                stepClass: 'running',
                numberStyle: 'background:rgba(251,191,36,0.2);color:#fbbf24;',
                statusIcon: null, // Show number
            },
            'delegated': {
                stepClass: 'delegated',
                numberStyle: 'background:rgba(167,139,250,0.2);color:#a78bfa;',
                statusIcon: '🤖',
            },
            'awaiting_input': {
                stepClass: 'awaiting',
                numberStyle: 'background:rgba(251,191,36,0.2);color:#fbbf24;',
                statusIcon: '💬',
            },
            'failed': {
                stepClass: 'failed',
                numberStyle: 'background:rgba(251,113,133,0.2);color:#fb7185;',
                statusIcon: '✗',
            },
            'skipped': {
                stepClass: 'skipped',
                numberStyle: 'background:#27272a;color:#71717a;',
                statusIcon: '—',
            },
            'pending': {
                stepClass: 'pending',
                numberStyle: '',
                statusIcon: null,
            },
        };
        return styles[status] || styles['pending'];
    }

    _renderSidebarPlan(plan) {
        if (!plan || !plan.steps || plan.steps.length === 0) return;

        this.sidebar.showRightSidebar();

        const statusColors = {
            pending: 'text-zinc-500',
            running: 'text-amber-400',
            completed: 'text-emerald-400',
            failed: 'text-rose-400',
        };

        let html = '';

        // Goal
        if (plan.goal) {
            html += `<div class="mb-4">
                <div class="section-label mb-1">GOAL</div>
                <p class="text-sm text-zinc-300">${this._escapeHtml(plan.goal).substring(0, 120)}</p>
            </div>`;
        }

        // Status
        const statusLabel = {
            draft: 'Awaiting Confirmation',
            executing: 'Executing...',
            completed: 'Completed',
            failed: 'Failed',
            partial: 'Partially Complete',
            delegated: 'Delegated to Agent',
            awaiting_agent_input: 'Awaiting Agent Response',
        }[plan.status] || plan.status.replace(/_/g, ' ');

        const statusColor = {
            draft: 'text-amber-400',
            executing: 'text-cyber-cyan',
            completed: 'text-emerald-400',
            failed: 'text-rose-400',
            partial: 'text-amber-400',
            delegated: 'text-cyber-violet',
            awaiting_agent_input: 'text-amber-400',
        }[plan.status] || 'text-zinc-400';

        html += `<div class="mb-4">
            <div class="section-label mb-1">STATUS</div>
            <span class="text-sm font-medium ${statusColor}">${statusLabel}</span>
        </div>`;

        // Steps
        html += `<div class="section-label mb-2">STEPS</div>`;
        html += `<div class="flex flex-col gap-2">`;

        plan.steps.forEach((step, i) => {
            // Determine visual style based on status
            const { stepClass, numberStyle, statusIcon } = this._getStepVisualStyle(step.status);

            html += `<div class="sidebar-step ${stepClass}">
                <div class="sidebar-step-number" style="${numberStyle}">${statusIcon || (i + 1)}</div>
                <div class="flex-1 min-w-0">
                    <div class="text-[13px] text-zinc-300">${this._escapeHtml(step.description)}</div>
                    ${step.domain ? `<span class="sidebar-step-domain">${step.domain}</span>` : ''}
                </div>
            </div>`;
        });

        html += `</div>`;

        this.sidebar.setPlanDetails(html);
    }

    // ─── Session Management ─────────────────────────────────

    async _createSession() {
        try {
            let session;
            // If we have an auth token, create session with user context
            if (this.authToken) {
                session = await this.api.createSessionWithToken(this.authToken, 'New Chat');
            } else {
                session = await this.api.createSession('New Chat');
            }
            this.currentSessionId = session.session_id;
            await this._refreshSessions();
        } catch (err) {
            console.error('Failed to create session:', err);
            this.currentSessionId = 'local_' + Date.now();
        }
    }

    async _handleNewChat() {
        this.currentSessionId = null;
        this.currentPlan = null;
        this.agentConversations = {};
        this.pendingAgentQuestion = null;
        this.currentAgentConversationId = null;
        this.chat.clear();
        this.chat.showWelcome();
        this._updateTitle('New Chat');
        this.sidebar.hideRightSidebar();
        this.sidebar.clearPlanDetails();
        this.inputBar.enable();
        this.inputBar.focus();
    }

    async _handleSessionSelect(sessionId) {
        this.currentSessionId = sessionId;
        this.sidebar.setActive(sessionId);
        const session = this.sessions.find(s => s.session_id === sessionId);
        this._updateTitle(session?.title || 'Chat');
        this.chat.clear();
        this.chat.hideWelcome();

        // Restore persisted messages
        try {
            const messages = await this.api.getMessages(sessionId);
            if (messages.length > 0) {
                for (const msg of messages) {
                    if (msg.role === 'user') {
                        this.chat.addUserMessage(msg.content);
                    } else if (msg.role === 'assistant') {
                        this.chat.addAIMessage(msg.content);
                    }
                }
            } else {
                this.chat.addAIMessage('Session restored. What would you like to do next?');
            }
        } catch (err) {
            console.error('Failed to load messages:', err);
            this.chat.addAIMessage('Session restored. What would you like to do next?');
        }
    }

    async _handleSessionDelete(sessionId) {
        try {
            await this.api.deleteSession(sessionId);
            if (this.currentSessionId === sessionId) this._handleNewChat();
            await this._refreshSessions();
        } catch (err) {
            console.error('Failed to delete session:', err);
        }
    }

    async _refreshSessions() {
        try {
            this.sessions = await this.api.listSessions();
            this.sidebar.renderSessions(this.sessions, this.currentSessionId);
        } catch { /* Backend not reachable yet */ }
    }

    // ─── Agent Conversation Handling ───────────────────────

    _updateAgentConversation(data) {
        // Track active agent conversations
        if (!this.agentConversations) {
            this.agentConversations = {};
        }

        // Log the full data for debugging
        console.log('[_updateAgentConversation] Storing conversation:', data.conversation_id, {
            status: data.status,
            messages: data.messages,
            message_count: data.message_count,
        });

        this.agentConversations[data.conversation_id] = data;

        // Update sidebar with agent conversation info
        this._renderAgentConversations();
    }

    _showAgentQuestion(data) {
        // Store the pending question for context
        this.pendingAgentQuestion = data.question;
        this.currentAgentConversationId = data.conversation_id;

        // The question should already be in the streamed response
        // But we can highlight it in the sidebar
        this._renderAgentConversations();
    }

    _renderAgentConversations() {
        if (!this.agentConversations || Object.keys(this.agentConversations).length === 0) {
            return;
        }

        // Show agent conversations in the right sidebar
        this.sidebar.showRightSidebar();

        // Build the HTML for agent conversations section
        let html = '<div id="agent-conversations-section" class="mb-4">';
        html += '<div class="section-label mb-2">AGENT CONVERSATIONS</div>';

        for (const [convId, conv] of Object.entries(this.agentConversations)) {
            const statusColors = {
                'active': 'text-cyber-cyan',
                'waiting_for_user': 'text-amber-400',
                'completed': 'text-emerald-400',
                'failed': 'text-rose-400',
            };
            const statusIcons = {
                'active': '🔄',
                'waiting_for_user': '💬',
                'completed': '✅',
                'failed': '❌',
            };

            const statusColor = statusColors[conv.status] || 'text-zinc-400';
            const statusIcon = statusIcons[conv.status] || '❓';
            const isCurrent = conv.is_current || convId === this.currentAgentConversationId;
            const isExpanded = this.expandedConversations?.[convId] || false;
            const messages = conv.messages || [];

            html += `<div class="rounded-lg bg-zinc-800/50 mb-2 ${isCurrent ? 'ring-1 ring-cyber-cyan/50' : ''} overflow-hidden">
                <div class="p-3 cursor-pointer hover:bg-zinc-700/30 transition-colors" data-conv-toggle="${convId}">
                    <div class="flex items-center justify-between mb-1">
                        <div class="flex items-center gap-2">
                            <span class="text-lg">${statusIcon}</span>
                            <span class="text-sm font-medium text-zinc-200">${this._escapeHtml(conv.agent_name || 'Unknown Agent')}</span>
                        </div>
                        <svg class="w-4 h-4 text-zinc-500 transition-transform ${isExpanded ? 'rotate-180' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
                        </svg>
                    </div>
                    <div class="text-xs text-zinc-500 mb-1">${this._escapeHtml(conv.task_summary || '')}</div>
                    <div class="flex items-center justify-between text-xs">
                        <span class="${statusColor}">${conv.status?.replace('_', ' ') || 'unknown'}</span>
                        <span class="text-zinc-600">${conv.message_count || messages.length} messages</span>
                    </div>
                </div>
                ${isExpanded ? this._renderConversationMessages(messages) : ''}
            </div>`;
        }

        html += '</div>';

        // Set this at the beginning of the plan-details container
        // We need to preserve existing plan content
        const planDetails = document.getElementById('plan-details');
        if (planDetails) {
            // Check if agent section already exists
            const existingSection = document.getElementById('agent-conversations-section');
            if (existingSection) {
                // Just update the existing section
                existingSection.outerHTML = html;
            } else {
                // Insert at the beginning
                planDetails.insertAdjacentHTML('afterbegin', html);
            }

            // Bind toggle events
            planDetails.querySelectorAll('[data-conv-toggle]').forEach(el => {
                el.onclick = (e) => {
                    const convId = el.dataset.convToggle;
                    if (!this.expandedConversations) this.expandedConversations = {};
                    this.expandedConversations[convId] = !this.expandedConversations[convId];
                    this._renderAgentConversations();
                };
            });
        }
    }

    _renderConversationMessages(messages) {
        console.log('[_renderConversationMessages] Rendering messages:', messages);

        if (!messages || messages.length === 0) {
            console.log('[_renderConversationMessages] No messages to render');
            return '<div class="px-3 pb-3 text-xs text-zinc-600 italic">No messages yet</div>';
        }

        let html = '<div class="border-t border-zinc-700/50 max-h-64 overflow-y-auto custom-scrollbar">';

        for (const msg of messages) {
            const isUser = msg.role === 'user' || msg.role === 'human';
            const roleLabel = isUser ? 'You' : 'Agent';
            const roleColor = isUser ? 'text-cyber-cyan' : 'text-cyber-violet';
            const bgColor = isUser ? 'bg-zinc-900/50' : 'bg-zinc-800/30';
            const content = msg.content || '';
            // Truncate long messages in the sidebar view
            const truncatedContent = content.length > 300 ? content.substring(0, 300) + '...' : content;

            html += `<div class="p-2 ${bgColor} border-b border-zinc-700/30 last:border-b-0">
                <div class="text-[10px] ${roleColor} font-medium mb-1">${roleLabel}</div>
                <div class="text-xs text-zinc-400 whitespace-pre-wrap break-words">${this._escapeHtml(truncatedContent)}</div>
            </div>`;
        }

        html += '</div>';
        return html;
    }

    // ─── Helpers ────────────────────────────────────────────

    _updateTitle(title) {
        const el = document.getElementById('chat-title');
        if (el) el.textContent = title;
    }

    _escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}


document.addEventListener('DOMContentLoaded', () => {
    window.app = new BuilderApp();
});
