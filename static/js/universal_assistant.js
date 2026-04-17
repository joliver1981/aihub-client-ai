/**
 * Universal Assistant Widget - Enhanced Version
 * A floating chat assistant that automatically extracts page context.
 * 
 * Features:
 * - Automatic page awareness (URL, title, forms, buttons, sections)
 * - Works on ANY page without configuration
 * - Optional page-specific context via window.assistantPageContext
 * - Large chat panel for real conversations
 */

(function() {
    'use strict';

    // ==========================================================================
    // Configuration
    // ==========================================================================
    
    const DEFAULT_CONFIG = {
        apiEndpoint: '/api/assistant/query',
        historyEndpoint: '/api/assistant/history',
        position: 'bottom-right',
        theme: 'auto',
        welcomeMessage: null,
        placeholder: 'Ask me anything about this page...',
        maxHistoryDisplay: 100
    };

    // ==========================================================================
    // Automatic Page Context Extraction
    // ==========================================================================

    class PageContextExtractor {
        static extract() {
            return {
                url: window.location.pathname,
                fullUrl: window.location.href,
                pageTitle: document.title,
                pageName: this.extractPageName(),
                sections: this.extractSections(),
                forms: this.extractForms(),
                buttons: this.extractButtons(),
                tables: this.extractTables(),
                selectedItems: this.extractSelectedItems(),
                activeTab: this.extractActiveTab(),
                modalOpen: this.extractModalState(),
                breadcrumbs: this.extractBreadcrumbs(),
                extractedAt: new Date().toISOString()
            };
        }

        static extractPageName() {
            const headerSelectors = [
                'h1', '.page-title', '.page-header h1', '.page-header h2',
                '.content-header h1', '.compact-header h4', '.page-header h4'
            ];
            
            for (const selector of headerSelectors) {
                const el = document.querySelector(selector);
                if (el && el.textContent.trim()) {
                    const text = el.textContent.trim();
                    if (text.length < 100) {
                        return text.split('\n')[0].trim();
                    }
                }
            }
            
            const path = window.location.pathname;
            if (path && path !== '/') {
                const parts = path.split('/').filter(p => p);
                if (parts.length > 0) {
                    const lastPart = parts[parts.length - 1];
                    return lastPart
                        .replace(/[-_]/g, ' ')
                        .replace(/\b\w/g, l => l.toUpperCase())
                        .replace(/\.html?$/i, '');
                }
            }
            
            const title = document.title;
            if (title) {
                return title.split(/\s*[-|]\s*/)[0].trim();
            }
            
            return 'Current Page';
        }

        static extractSections() {
            const sections = [];
            const sectionSelectors = [
                '.card-header', '.section-header', '.panel-heading',
                'h2', 'h3', '.collapse-header', 'legend'
            ];
            
            const seen = new Set();
            
            for (const selector of sectionSelectors) {
                document.querySelectorAll(selector).forEach(el => {
                    let text = el.textContent.trim().split('\n')[0].trim();
                    text = text.replace(/[\u25BC\u25B6\u25B2\u25C0]/g, '').trim();
                    
                    if (text && text.length < 80 && !seen.has(text.toLowerCase())) {
                        seen.add(text.toLowerCase());
                        sections.push(text);
                    }
                });
            }
            
            return sections.slice(0, 15);
        }

        static extractForms() {
            const forms = [];
            const fieldSelectors = 'input:not([type="hidden"]), select, textarea';
            const fields = document.querySelectorAll(fieldSelectors);
            
            fields.forEach(field => {
                if (field.offsetParent === null && field.type !== 'hidden') return;
                
                // Skip checkboxes entirely - they're handled by custom page context
                // This prevents confusion between auto-extracted and custom context
                if (field.type === 'checkbox' || field.type === 'radio') {
                    return;
                }
                
                let label = '';
                
                if (field.id) {
                    const labelEl = document.querySelector(`label[for="${field.id}"]`);
                    if (labelEl) label = labelEl.textContent.trim();
                }
                
                if (!label) {
                    const parentLabel = field.closest('label');
                    if (parentLabel) {
                        label = parentLabel.textContent.replace(field.value || '', '').trim();
                    }
                }
                
                if (!label && field.placeholder) {
                    label = field.placeholder;
                }
                
                if (!label) {
                    label = field.name || field.id || '';
                    label = label.replace(/[-_]/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
                }
                
                if (label && label.length < 50) {
                    const fieldInfo = {
                        label: label.split('\n')[0].trim(),
                        type: field.type || field.tagName.toLowerCase(),
                        hasValue: !!field.value
                    };
                    
                    if (field.tagName === 'SELECT') {
                        fieldInfo.optionCount = field.options.length;
                        fieldInfo.selectedOption = field.options[field.selectedIndex]?.text;
                    }
                    
                    forms.push(fieldInfo);
                }
            });
            
            return forms.slice(0, 20);
        }

        static extractButtons() {
            const buttons = [];
            const seen = new Set();
            
            const buttonSelectors = [
                'button:not([disabled])',
                'input[type="submit"]',
                'input[type="button"]',
                '.btn',
                'a.btn'
            ];
            
            buttonSelectors.forEach(selector => {
                document.querySelectorAll(selector).forEach(btn => {
                    if (btn.offsetParent === null) return;
                    
                    let text = btn.textContent.trim() || btn.value || btn.title || '';
                    text = text.split('\n')[0].trim();
                    
                    if (text.length < 2 || text.length > 50) return;
                    
                    const textLower = text.toLowerCase();
                    if (!seen.has(textLower)) {
                        seen.add(textLower);
                        buttons.push({
                            text: text,
                            type: btn.classList.contains('btn-primary') ? 'primary' :
                                  btn.classList.contains('btn-success') ? 'success' :
                                  btn.classList.contains('btn-danger') ? 'danger' :
                                  btn.classList.contains('btn-warning') ? 'warning' : 'default'
                        });
                    }
                });
            });
            
            return buttons.slice(0, 15);
        }

        static extractTables() {
            const tables = [];
            
            document.querySelectorAll('table').forEach(table => {
                if (table.offsetParent === null) return;
                
                const headers = [];
                table.querySelectorAll('th').forEach(th => {
                    const text = th.textContent.trim();
                    if (text && text.length < 50) {
                        headers.push(text);
                    }
                });
                
                if (headers.length > 0) {
                    tables.push({
                        columns: headers.slice(0, 10),
                        rowCount: table.querySelectorAll('tbody tr').length
                    });
                }
            });
            
            return tables.slice(0, 5);
        }

        static extractSelectedItems() {
            const selected = [];
            
            const selectors = [
                '.selected', '.active:not(.nav-link)', '.checked',
                'tr.selected', 'tr.active', '.list-group-item.active',
                '[aria-selected="true"]', ':checked'
            ];
            
            selectors.forEach(selector => {
                try {
                    document.querySelectorAll(selector).forEach(el => {
                        if (el.type === 'checkbox' || el.type === 'radio') {
                            const label = document.querySelector(`label[for="${el.id}"]`);
                            if (label) {
                                selected.push(label.textContent.trim());
                            }
                        } else {
                            const text = el.textContent.trim().split('\n')[0];
                            if (text && text.length < 100) {
                                selected.push(text);
                            }
                        }
                    });
                } catch (e) {}
            });
            
            return [...new Set(selected)].slice(0, 10);
        }

        static extractActiveTab() {
            const activeTab = document.querySelector('.nav-tabs .nav-link.active, .nav-pills .nav-link.active');
            if (activeTab) {
                return activeTab.textContent.trim();
            }
            return null;
        }

        static extractModalState() {
            const modal = document.querySelector('.modal.show, .modal[style*="display: block"]');
            if (modal) {
                const title = modal.querySelector('.modal-title');
                return {
                    isOpen: true,
                    title: title ? title.textContent.trim() : 'Dialog'
                };
            }
            return { isOpen: false };
        }

        static extractBreadcrumbs() {
            const breadcrumbs = [];
            document.querySelectorAll('.breadcrumb-item, .breadcrumb a').forEach(el => {
                const text = el.textContent.trim();
                if (text && text.length < 50) {
                    breadcrumbs.push(text);
                }
            });
            return breadcrumbs;
        }
    }

    // ==========================================================================
    // Session Management
    // ==========================================================================
    
    function getSessionId() {
        let sessionId = sessionStorage.getItem('assistant_session_id');
        if (!sessionId) {
            sessionId = 'session-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9);
            sessionStorage.setItem('assistant_session_id', sessionId);
        }
        return sessionId;
    }

    // ==========================================================================
    // Universal Assistant Class
    // ==========================================================================
    
    class UniversalAssistant {
        constructor(config = {}) {
            this.config = { ...DEFAULT_CONFIG, ...window.assistantConfig, ...config };
            this.sessionId = getSessionId();
            this.isOpen = false;
            this.isProcessing = false;
            this.messages = [];
            this.pageContext = null;
            
            this.init();
        }

        init() {
            this.refreshPageContext();
            this.createWidget();
            this.attachEventListeners();
            this.loadConversationHistory();
            
            // Re-extract context periodically
            setInterval(() => this.refreshPageContext(), 30000);
        }

        refreshPageContext() {
            this.pageContext = PageContextExtractor.extract();
        }

        createWidget() {
            const container = document.createElement('div');
            container.id = 'ua-assistant-container';
            container.className = `ua-container ua-${this.config.position} ua-theme-${this.getTheme()}`;
            
            const pageName = this.pageContext?.pageName || 'this page';
            
            container.innerHTML = `
                <!-- Toggle Button -->
                <button class="ua-toggle-btn" id="ua-toggle-btn" title="AI Assistant (Ctrl+Shift+A)">
                    <svg class="ua-icon ua-icon-chat" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.17L4 17.17V4h16v12z"/>
                        <path d="M7 9h2v2H7zm4 0h2v2h-2zm4 0h2v2h-2z"/>
                    </svg>
                    <svg class="ua-icon ua-icon-close" viewBox="0 0 24 24" fill="currentColor" style="display:none;">
                        <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/>
                    </svg>
                </button>

                <!-- Chat Panel -->
                <div class="ua-panel" id="ua-panel">
                    <!-- Header -->
                    <div class="ua-header">
                        <div class="ua-header-content">
                            <svg class="ua-header-icon" viewBox="0 0 24 24" fill="currentColor">
                                <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/>
                            </svg>
                            <span class="ua-header-title">AI Assistant</span>
                        </div>
                        <div class="ua-header-actions">
                            <button class="ua-header-btn" id="ua-clear-btn" title="New conversation">
                                <svg viewBox="0 0 24 24" fill="currentColor" width="16" height="16">
                                    <path d="M17.65 6.35A7.958 7.958 0 0012 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0112 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/>
                                </svg>
                            </button>
                            <button class="ua-header-btn" id="ua-minimize-btn" title="Minimize">
                                <svg viewBox="0 0 24 24" fill="currentColor" width="16" height="16">
                                    <path d="M19 13H5v-2h14v2z"/>
                                </svg>
                            </button>
                        </div>
                    </div>
                    
                    <!-- Page Context Indicator -->
                    <div class="ua-context-bar" id="ua-context-bar">
                        <span class="ua-context-icon">📍</span>
                        <span class="ua-context-page" id="ua-context-page">${this.escapeHtml(pageName)}</span>
                        <span class="ua-context-status" id="ua-context-status" title="Page context active">●</span>
                    </div>

                    <!-- Messages -->
                    <div class="ua-messages" id="ua-messages">
                    </div>

                    <!-- Input Area -->
                    <div class="ua-input-area">
                        <div class="ua-input-wrapper">
                            <textarea 
                                id="ua-input" 
                                class="ua-input" 
                                placeholder="${this.config.placeholder}"
                                rows="1"
                            ></textarea>
                            <button class="ua-send-btn" id="ua-send-btn" title="Send message">
                                <svg viewBox="0 0 24 24" fill="currentColor">
                                    <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
                                </svg>
                            </button>
                        </div>
                        <div class="ua-input-hint">
                            Press Enter to send • Shift+Enter for new line
                        </div>
                    </div>
                </div>
            `;

            document.body.appendChild(container);
            this.container = container;
            this.panel = document.getElementById('ua-panel');
            this.messagesContainer = document.getElementById('ua-messages');
            this.input = document.getElementById('ua-input');
        }

        getTheme() {
            if (this.config.theme === 'auto') {
                if (document.body.classList.contains('theme-dark')) return 'dark';
                if (document.body.classList.contains('theme-light')) return 'light';
                return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
            }
            return this.config.theme;
        }

        generateWelcomeMessage() {
            const ctx = this.pageContext;
            const pageName = ctx?.pageName || 'this page';
            
            return `Hi! I'm your AI assistant. I can see you're on **${pageName}**. How can I help you?`;
        }

        attachEventListeners() {
            document.getElementById('ua-toggle-btn').addEventListener('click', () => this.toggle());
            document.getElementById('ua-minimize-btn').addEventListener('click', () => this.close());
            document.getElementById('ua-clear-btn').addEventListener('click', () => this.startNewConversation());
            document.getElementById('ua-send-btn').addEventListener('click', () => this.sendMessage());
            
            this.input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    this.sendMessage();
                }
            });
            
            // Auto-resize only when typing (not on init)
            this.input.addEventListener('input', () => this.autoResizeInput());
            
            // Keyboard shortcut
            document.addEventListener('keydown', (e) => {
                if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'a') {
                    e.preventDefault();
                    this.toggle();
                }
            });
            
            // Theme observer
            const observer = new MutationObserver(() => {
                this.container.className = `ua-container ua-${this.config.position} ua-theme-${this.getTheme()}`;
            });
            observer.observe(document.body, { attributes: true, attributeFilter: ['class'] });
        }

        startNewConversation() {
            if (this.messages.length === 0) return;
            
            //if (!confirm('Start a new conversation? This will clear the current chat.')) return;
            
            // Clear server-side history
            fetch(`${this.config.historyEndpoint}?session_id=${this.sessionId}`, {
                method: 'DELETE'
            }).catch(() => {});
            
            // Clear local state
            this.messages = [];
            this.messagesContainer.innerHTML = '';
            
            // Generate new session
            this.sessionId = 'session-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9);
            sessionStorage.setItem('assistant_session_id', this.sessionId);
            
            // Refresh context and show welcome
            this.refreshPageContext();
            this.updateContextBar();
            this.addMessage(this.generateWelcomeMessage(), 'assistant', false);
        }

        updateContextBar() {
            const pageEl = document.getElementById('ua-context-page');
            const statusEl = document.getElementById('ua-context-status');
            
            if (pageEl && this.pageContext) {
                pageEl.textContent = this.pageContext.pageName || 'Current Page';
            }
            
            if (statusEl) {
                statusEl.style.color = '#4caf50';
                statusEl.title = 'Page context active';
            }
        }

        autoResizeInput() {
            const input = this.input;
            input.style.height = '24px';
            if (input.scrollHeight > 24) {
                input.style.height = Math.min(input.scrollHeight, 120) + 'px';
            }
        }

        toggle() {
            if (this.isOpen) {
                this.close();
            } else {
                this.open();
            }
        }

        open() {
            this.isOpen = true;
            this.container.classList.add('ua-open');
            
            this.container.querySelector('.ua-icon-chat').style.display = 'none';
            this.container.querySelector('.ua-icon-close').style.display = 'block';
            
            this.refreshPageContext();
            this.updateContextBar();
            
            if (this.messages.length === 0) {
                this.addMessage(this.generateWelcomeMessage(), 'assistant', false);
            }
            
            setTimeout(() => this.input.focus(), 300);
        }

        close() {
            this.isOpen = false;
            this.container.classList.remove('ua-open');
            
            this.container.querySelector('.ua-icon-chat').style.display = 'block';
            this.container.querySelector('.ua-icon-close').style.display = 'none';
        }

        async sendMessage() {
            const text = this.input.value.trim();
            if (!text || this.isProcessing) return;

            this.input.value = '';
            this.input.style.height = '24px'; // Reset to single line
            this.addMessage(text, 'user');
            this.showTypingIndicator();
            this.isProcessing = true;
            this.updateSendButton();

            try {
                this.refreshPageContext();
                
                const customContext = window.assistantPageContext || {};
                const customPageData = typeof customContext.getPageData === 'function' 
                    ? customContext.getPageData() 
                    : customContext.pageData || {};

                const fullContext = {
                    auto: this.pageContext,
                    custom: customPageData,
                    page: customContext.page || this.pageContext?.url?.split('/').filter(p => p).pop() || 'general'
                };

                const requestBody = {
                    question: text,
                    page: fullContext.page,
                    session_id: this.sessionId,
                    page_data: fullContext,
                    include_history: true
                };

                const response = await fetch(this.config.apiEndpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(requestBody)
                });

                const data = await response.json();
                this.removeTypingIndicator();

                if (data.status === 'error') {
                    this.addMessage(`Sorry, I encountered an error: ${data.error}`, 'assistant', true);
                } else {
                    this.addMessage(data.response, 'assistant');
                }

            } catch (error) {
                console.error('Assistant error:', error);
                this.removeTypingIndicator();
                this.addMessage('Sorry, I encountered a connection error. Please try again.', 'assistant', true);
            } finally {
                this.isProcessing = false;
                this.updateSendButton();
                this.input.focus();
            }
        }

        addMessage(text, sender, isError = false) {
            this.messages.push({ text, sender, isError, timestamp: new Date() });
            
            if (this.messages.length > this.config.maxHistoryDisplay) {
                this.messages.shift();
            }

            const messageDiv = document.createElement('div');
            messageDiv.className = `ua-message ua-message-${sender}${isError ? ' ua-message-error' : ''}`;
            
            const formattedText = sender === 'assistant' ? this.formatMessage(text) : this.escapeHtml(text);
            
            messageDiv.innerHTML = `
                <div class="ua-message-avatar">
                    ${sender === 'user' ? this.getUserIcon() : this.getAssistantIcon()}
                </div>
                <div class="ua-message-content">
                    <div class="ua-message-text">${formattedText}</div>
                    <div class="ua-message-time">${this.formatTime(new Date())}</div>
                </div>
            `;

            this.messagesContainer.appendChild(messageDiv);
            this.scrollToBottom();

            messageDiv.style.opacity = '0';
            messageDiv.style.transform = 'translateY(10px)';
            requestAnimationFrame(() => {
                messageDiv.style.transition = 'opacity 0.3s, transform 0.3s';
                messageDiv.style.opacity = '1';
                messageDiv.style.transform = 'translateY(0)';
            });
        }

        formatMessage(text) {
            let formatted = this.escapeHtml(text);
            formatted = formatted.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
            formatted = formatted.replace(/\*(.*?)\*/g, '<em>$1</em>');
            formatted = formatted.replace(/`([^`]+)`/g, '<code>$1</code>');
            formatted = formatted.replace(/```(\w*)\n?([\s\S]*?)```/g, (match, lang, code) => {
                return `<pre><code class="language-${lang || 'plaintext'}">${code.trim()}</code></pre>`;
            });
            formatted = formatted.replace(/\n/g, '<br>');
            formatted = formatted.replace(/^- (.+)$/gm, '• $1');
            return formatted;
        }

        escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        formatTime(date) {
            return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        }

        getUserIcon() {
            return `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>`;
        }

        getAssistantIcon() {
            return `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/></svg>`;
        }

        showTypingIndicator() {
            // Inject keyframes if not already present
            if (!document.getElementById('ua-typing-keyframes')) {
                const style = document.createElement('style');
                style.id = 'ua-typing-keyframes';
                style.textContent = `
                    @keyframes ua-dot-bounce {
                        0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
                        40% { transform: scale(1); opacity: 1; }
                    }
                `;
                document.head.appendChild(style);
            }
            
            const indicator = document.createElement('div');
            indicator.className = 'ua-message ua-message-assistant ua-typing-indicator';
            indicator.id = 'ua-typing-indicator';
            
            // Create dots container
            const dotsContainer = document.createElement('div');
            dotsContainer.style.cssText = 'display:flex;align-items:center;gap:6px;padding:14px 18px;background-color:#f5f5f5;border-radius:18px;border-bottom-left-radius:6px;';
            
            // Create three animated dots
            for (let i = 0; i < 3; i++) {
                const dot = document.createElement('span');
                dot.style.cssText = `
                    width: 10px;
                    height: 10px;
                    background-color: #3498db;
                    border-radius: 50%;
                    display: inline-block;
                    animation: ua-dot-bounce 1.4s infinite ease-in-out both;
                    animation-delay: ${i * 0.16}s;
                `.replace(/\s+/g, ' ');
                dotsContainer.appendChild(dot);
            }
            
            indicator.innerHTML = `
                <div class="ua-message-avatar">${this.getAssistantIcon()}</div>
                <div class="ua-message-content"></div>
            `;
            indicator.querySelector('.ua-message-content').appendChild(dotsContainer);
            
            this.messagesContainer.appendChild(indicator);
            this.scrollToBottom();
        }

        removeTypingIndicator() {
            const indicator = document.getElementById('ua-typing-indicator');
            if (indicator) indicator.remove();
        }

        updateSendButton() {
            const btn = document.getElementById('ua-send-btn');
            btn.disabled = this.isProcessing;
            btn.style.opacity = this.isProcessing ? '0.5' : '1';
        }

        scrollToBottom() {
            this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
        }

        async loadConversationHistory() {
            try {
                const response = await fetch(`${this.config.historyEndpoint}?session_id=${this.sessionId}`);
                const data = await response.json();
                
                if (data.status === 'success' && data.history && data.history.length > 0) {
                    data.history.forEach(msg => {
                        this.messages.push({
                            text: msg.content,
                            sender: msg.role,
                            timestamp: new Date(msg.timestamp)
                        });
                    });
                    this.renderMessages();
                }
            } catch (error) {
                console.log('Could not load conversation history:', error);
            }
        }

        renderMessages() {
            this.messagesContainer.innerHTML = '';
            this.messages.forEach(msg => {
                const messageDiv = document.createElement('div');
                messageDiv.className = `ua-message ua-message-${msg.sender}${msg.isError ? ' ua-message-error' : ''}`;
                
                const formattedText = msg.sender === 'assistant' ? this.formatMessage(msg.text) : this.escapeHtml(msg.text);
                
                messageDiv.innerHTML = `
                    <div class="ua-message-avatar">
                        ${msg.sender === 'user' ? this.getUserIcon() : this.getAssistantIcon()}
                    </div>
                    <div class="ua-message-content">
                        <div class="ua-message-text">${formattedText}</div>
                        <div class="ua-message-time">${this.formatTime(msg.timestamp)}</div>
                    </div>
                `;
                this.messagesContainer.appendChild(messageDiv);
            });
            this.scrollToBottom();
        }
    }

    // ==========================================================================
    // Initialization
    // ==========================================================================
    
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAssistant);
    } else {
        initAssistant();
    }

    function initAssistant() {
        if (window.assistantDisabled === true) {
            console.log('Universal Assistant disabled');
            return;
        }
        
        window.universalAssistant = new UniversalAssistant();
        console.log('Universal Assistant initialized with automatic page context');
    }

    window.UniversalAssistant = UniversalAssistant;
    window.PageContextExtractor = PageContextExtractor;

})();
