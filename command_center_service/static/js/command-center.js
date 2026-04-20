/**
 * Command Center — Main Application
 * SSE streaming, session management, state handling.
 */

const CC = {
    sessionId: null,
    userId: null,
    userContext: null,
    isStreaming: false,
    lastTraceId: null,
    /** @type {Array<{file_id: string, filename: string, size: number, content_type: string}>} */
    _stagedFiles: [],
    // Token refresh state
    _tokenExpiresAt: null,
    _refreshTimer: null,
    _TOKEN_REFRESH_INTERVAL: 45 * 60 * 1000,   // Check every 45 minutes
    _TOKEN_REFRESH_THRESHOLD: 60 * 60 * 1000,   // Refresh when < 1 hour remaining

    async init() {
        // Try to get user context from URL params or localStorage
        const params = new URLSearchParams(window.location.search);
        this.sessionId = params.get('session_id') || localStorage.getItem('cc_session_id') || null;

        // Try to get user context from token (main app auth flow)
        const token = params.get('token');
        if (token) {
            try {
                const resp = await fetch('/api/auth/validate-token', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({token})
                });
                const data = await resp.json();
                if (data.valid && data.user_context) {
                    this.userId = data.user_context.user_id || null;
                    this.userContext = data.user_context;
                    // Default 4-hour expiry; server may return a different value
                    this._tokenExpiresAt = Date.now() + (data.expires_in || 14400) * 1000;
                    if (this.userId) {
                        localStorage.setItem('cc_user_id', String(this.userId));
                        localStorage.setItem('cc_user_context', JSON.stringify(data.user_context));
                        localStorage.setItem('cc_token_expires', String(this._tokenExpiresAt));
                    }
                }
            } catch (e) {
                console.warn('Token validation failed:', e);
            }
        }

        // Fallback: restore cached user context (includes role for permission checks)
        if (!this.userId) {
            this.userId = params.get('user_id') || localStorage.getItem('cc_user_id') || null;
        }
        if (!this.userContext) {
            try {
                const cached = localStorage.getItem('cc_user_context');
                if (cached) this.userContext = JSON.parse(cached);
            } catch (e) { /* ignore parse errors */ }
        }
        // Restore token expiry from cache
        if (!this._tokenExpiresAt) {
            const expiresStr = localStorage.getItem('cc_token_expires');
            if (expiresStr) this._tokenExpiresAt = parseInt(expiresStr, 10);
        }

        // Start proactive token refresh (keeps role/permissions current)
        this._startTokenRefresh();

        // Initialize memory/suggestions
        CCMemory.init(this.userId);

        // Load sessions
        this.loadSessions();

        // Load plugins
        this.loadPlugins();

        // If we have a session, load its messages
        if (this.sessionId) {
            this.loadSession(this.sessionId);
        }

        // Focus input
        document.getElementById('user-input').focus();

        // Initialize file upload
        this._initUpload();
    },

    // ── Token refresh ──────────────────────────────────────────────
    async _refreshToken() {
        if (!this.userId) return;
        try {
            const resp = await fetch('/api/auth/refresh-token', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ user_id: parseInt(this.userId) })
            });
            const data = await resp.json();
            if (data.valid && data.token) {
                this._tokenExpiresAt = Date.now() + (data.expires_in || 14400) * 1000;
                localStorage.setItem('cc_token_expires', String(this._tokenExpiresAt));
                if (data.user_context) {
                    this.userContext = data.user_context;
                    this.userId = data.user_context.user_id || this.userId;
                    localStorage.setItem('cc_user_context', JSON.stringify(data.user_context));
                    localStorage.setItem('cc_user_id', String(this.userId));
                }
                console.log('CC token refreshed successfully');
            }
        } catch (e) {
            // Network error — will retry on next interval or tab focus
            console.warn('Token refresh error:', e);
        }
    },

    // Returns true when the current user has Developer (2) or Admin (3) role.
    // Role model (from role_decorators.py): 1 = User, 2 = Developer, 3 = Admin.
    // Treats missing/null userContext as "deny" so developer-only UI stays
    // hidden during the brief window before token validation completes.
    _hasDevRole() {
        const role = this.userContext && this.userContext.role;
        return typeof role === 'number' && role >= 2;
    },

    _startTokenRefresh() {
        // Periodic check every 45 minutes
        this._refreshTimer = setInterval(() => {
            const remaining = (this._tokenExpiresAt || 0) - Date.now();
            if (remaining < this._TOKEN_REFRESH_THRESHOLD) {
                this._refreshToken();
            }
        }, this._TOKEN_REFRESH_INTERVAL);

        // Refresh when user returns to tab after being away
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible' && this.userId) {
                const remaining = (this._tokenExpiresAt || 0) - Date.now();
                if (remaining < this._TOKEN_REFRESH_THRESHOLD) {
                    this._refreshToken();
                }
            }
        });

        // If token is already expired or close to expiring on load, refresh now
        if (this.userId) {
            const remaining = (this._tokenExpiresAt || 0) - Date.now();
            if (remaining < this._TOKEN_REFRESH_THRESHOLD) {
                this._refreshToken();
            }
        }
    },

    // ── Chat ────────────────────────────────────────────────────────
    async send() {
        const input = document.getElementById('user-input');
        const message = input.value.trim();
        if (!message && this._stagedFiles.length === 0) return;
        if (this.isStreaming) return;

        // Collect attachments before clearing
        const attachments = this._stagedFiles.map(f => f.file_id);
        const fileNames = this._stagedFiles.map(f => f.filename);

        input.value = '';
        this._stagedFiles = [];
        this._renderStagedFiles();
        this.isStreaming = true;
        this._setStatus('thinking', 'Analyzing your request...');
        document.querySelector('.cc-btn-send').disabled = true;

        // Build display message (show file names if attached)
        let displayMsg = message;
        if (fileNames.length > 0) {
            const fileList = fileNames.map(n => `📎 ${n}`).join(', ');
            displayMsg = message ? `${message}\n${fileList}` : fileList;
        }

        // Add user message to UI
        this._addMessage('user', displayMsg);

        try {
            const body = {
                message: message || `Analyze the attached file(s): ${fileNames.join(', ')}`,
                session_id: this.sessionId,
                user_context: this.userContext || (this.userId ? { user_id: parseInt(this.userId) } : null),
            };
            if (attachments.length > 0) {
                body.attachments = attachments;
            }

            const resp = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });

            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

            // Process SSE stream
            const reader = resp.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            let currentEventType = null;

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop(); // Keep incomplete line

                for (const line of lines) {
                    if (line.startsWith('event: ')) {
                        currentEventType = line.substring(7).trim();
                        continue;
                    }
                    if (line.startsWith('data: ')) {
                        try {
                            const data = JSON.parse(line.substring(6));
                            data._eventType = currentEventType;
                            this._handleEvent(data);
                        } catch (e) {
                            console.warn('SSE parse error:', e, line.substring(0, 100));
                        }
                        currentEventType = null;
                    }
                    if (line.trim() === '') {
                        currentEventType = null;
                    }
                }
            }

            // Process any remaining buffer
            if (buffer.startsWith('data: ')) {
                try {
                    const data = JSON.parse(buffer.substring(6));
                    this._handleEvent(data);
                } catch (e) {}
            }

        } catch (e) {
            console.error('Chat error:', e);
            this._addMessage('assistant', `Error: ${e.message}`);
        } finally {
            this.isStreaming = false;
            this._setStatus('ready', '');
            document.querySelector('.cc-btn-send').disabled = false;
            document.getElementById('user-input').focus();
            // Refresh sidebar to pick up auto-generated title
            this.loadSessions();
        }
    },

    _handleEvent(data) {
        // The SSE response wraps events like: {session_id, blocks, tasks, etc.}
        if (data.session_id && !this.sessionId) {
            this.sessionId = data.session_id;
            localStorage.setItem('cc_session_id', this.sessionId);
        }
        if (data.title) {
            document.getElementById('chat-title').textContent = data.title;
        }

        if (data._eventType === 'trace' && data.trace_id) {
            this.lastTraceId = data.trace_id;
        }

        if (data.blocks) {
            this._removeThinkingBubble();
            this._setStatus('ready', '');
            const traceId = data.trace_id || this.lastTraceId;
            this._addRichMessage(data.blocks, traceId);
        }

        if (data.phase) {
            this._setStatus('thinking', data.message || data.phase);
        }

        if (data.tasks) {
            this._showTasks(data.tasks);
        }

        if (data.log && data._eventType === 'builder_log') {
            this._showBuilderLog(data.log);
        }

        if (data.message && !data.blocks && !data.phase) {
            // Error or plain message
            this._addMessage('assistant', data.message);
        }
    },

    _showBuilderLog(log) {
        const panel = document.getElementById('right-panel');
        const section = document.getElementById('builder-log-section');
        const container = document.getElementById('builder-log');
        if (!panel || !section || !container) return;

        panel.style.display = '';
        section.style.display = '';
        container.innerHTML = '';

        for (const entry of log) {
            const div = document.createElement('div');
            div.className = `cc-log-entry ${entry.role || ''}`;
            const text = entry.content || '';
            // Truncate long entries
            div.textContent = text.length > 500 ? text.substring(0, 500) + '...' : text;
            container.appendChild(div);
        }
        container.scrollTop = container.scrollHeight;
    },

    _addMessage(role, content) {
        const container = document.getElementById('messages');
        const div = document.createElement('div');
        div.className = `cc-message ${role}`;

        if (role === 'assistant') {
            const cleanContent = this._unwrapJsonContent(content);
            if (window.marked) {
                div.innerHTML = marked.parse(cleanContent);
            } else {
                div.textContent = cleanContent;
            }
        } else {
            div.textContent = content;
        }

        container.appendChild(div);
        container.scrollTop = container.scrollHeight;
    },

    /** Recursively unwrap JSON block wrappers, returning plain markdown text. */
    _unwrapJsonContent(text) {
        if (!text) return '';
        const trimmed = text.trim();
        // Unwrap JSON blocks array
        if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
            try {
                const arr = JSON.parse(trimmed);
                if (Array.isArray(arr) && arr.length > 0 && arr[0] && typeof arr[0] === 'object' && arr[0].type) {
                    const parts = arr
                        .filter(b => b && typeof b === 'object')
                        .map(b => {
                            const c = b.content || b.text || '';
                            return this._unwrapJsonContent(c);
                        });
                    if (parts.length > 0 && parts.some(p => p.length > 0)) {
                        return parts.join('\n\n');
                    }
                }
            } catch(e) {}
        }
        // Fence any remaining raw JSON object
        if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
            try {
                const obj = JSON.parse(trimmed);
                return '```json\n' + JSON.stringify(obj, null, 2) + '\n```';
            } catch(e) {}
        }
        return text;
    },

    _addRichMessage(blocks, traceId = null) {
        const container = document.getElementById('messages');
        const div = document.createElement('div');
        div.className = 'cc-message assistant';

        // Header actions (Inspect) — developer/admin only
        if (traceId && this._hasDevRole()) {
            const header = document.createElement('div');
            header.className = 'cc-message-actions';

            const btn = document.createElement('button');
            btn.className = 'cc-inspect-btn';
            btn.textContent = 'Inspect';
            btn.title = 'Open execution inspector for this response';
            btn.onclick = () => {
                const uid = this.userId || localStorage.getItem('cc_user_id') || 'anon';
                const sid = this.sessionId || localStorage.getItem('cc_session_id') || '';
                const url = `/static/inspect.html?trace_id=${encodeURIComponent(traceId)}&user_id=${encodeURIComponent(uid)}&session_id=${encodeURIComponent(sid)}`;
                window.open(url, '_blank');
            };

            header.appendChild(btn);
            div.appendChild(header);
        }

        CCRenderers.renderBlocks(blocks, div);

        container.appendChild(div);
        container.scrollTop = container.scrollHeight;
    },

    _setStatus(state, text) {
        const el = document.getElementById('status-indicator');
        if (state === 'thinking' || state === 'scanning' || state === 'processing') {
            el.className = 'cc-status thinking';
            el.innerHTML = `<span class="dot"></span> ${text}`;
            // Show inline loading indicator in chat
            this._showThinkingBubble(text);
        } else {
            el.className = 'cc-status';
            el.innerHTML = '<span class="dot"></span> Ready';
            this._removeThinkingBubble();
        }
    },

    _showThinkingBubble(text) {
        this._removeThinkingBubble();
        const container = document.getElementById('messages');
        const bubble = document.createElement('div');
        bubble.className = 'cc-message assistant cc-thinking-bubble';
        bubble.innerHTML = `
            <div class="cc-thinking-indicator">
                <div class="cc-thinking-dots">
                    <span></span><span></span><span></span>
                </div>
                <div class="cc-thinking-text">${text}</div>
            </div>
        `;
        container.appendChild(bubble);
        container.scrollTop = container.scrollHeight;
    },

    _removeThinkingBubble() {
        const existing = document.querySelector('.cc-thinking-bubble');
        if (existing) existing.remove();
    },

    _showTasks(tasks) {
        const panel = document.getElementById('right-panel');
        const list = document.getElementById('task-list');
        panel.style.display = 'block';
        list.innerHTML = '';

        tasks.forEach(t => {
            const item = document.createElement('div');
            item.className = 'cc-task-item';
            item.innerHTML = `
                <span class="cc-task-status ${t.status}"></span>
                <span>${t.description} ${t.agent ? `<span style="color:var(--cc-text-muted)">(${t.agent})</span>` : ''}</span>
            `;
            list.appendChild(item);
        });
    },

    // Build a query-string of the current user context so every session
    // endpoint call is scoped to the logged-in user. Without this the
    // server falls back to "anonymous" filtering which hides everything
    // (for legacy sessions that have no owner, only admins see them).
    _ownerQS() {
        const u = this._userCtx || {};
        const qp = new URLSearchParams();
        if (u.user_id !== undefined && u.user_id !== null) qp.set('user_id', String(u.user_id));
        if (u.tenant_id !== undefined && u.tenant_id !== null) qp.set('tenant_id', String(u.tenant_id));
        if (u.role !== undefined && u.role !== null) qp.set('role', String(u.role));
        if (u.username) qp.set('username', String(u.username));
        if (u.name) qp.set('name', String(u.name));
        return qp.toString();
    },

    async createSession() {
        try {
            const qs = this._ownerQS();
            const resp = await fetch(`/api/sessions${qs ? '?' + qs : ''}`, { method: 'POST' });
            const session = await resp.json();
            this.sessionId = session.session_id;
            localStorage.setItem('cc_session_id', this.sessionId);

            document.getElementById('messages').innerHTML = '';
            document.getElementById('chat-title').textContent = 'New Chat';
            document.getElementById('right-panel').style.display = 'none';

            this.loadSessions();
        } catch (e) {
            console.error('Failed to create session:', e);
        }
    },

    async loadSessions() {
        try {
            const qs = this._ownerQS();
            const resp = await fetch(`/api/sessions${qs ? '?' + qs : ''}`);
            const sessions = await resp.json();
            const list = document.getElementById('session-list');
            list.innerHTML = '';

            sessions.forEach(s => {
                // Hide empty sessions (0 messages) unless it's the current one
                if ((!s.message_count || s.message_count === 0) && s.session_id !== this.sessionId) return;

                const item = document.createElement('div');
                item.className = `cc-session-item ${s.session_id === this.sessionId ? 'active' : ''} ${s.is_pinned ? 'pinned' : ''}`;
                item.innerHTML = `
                    <div class="cc-session-info">
                        <div class="title">${s.title}</div>
                        <div class="meta">${s.message_count || 0} messages</div>
                    </div>
                    <div class="cc-session-actions">
                        <button class="cc-session-pin ${s.is_pinned ? 'active' : ''}" onclick="event.stopPropagation(); CC.togglePinSession('${s.session_id}')" title="${s.is_pinned ? 'Unpin' : 'Pin'} session">📌</button>
                        <button class="cc-session-delete" onclick="event.stopPropagation(); CC.deleteSession('${s.session_id}')" title="Delete session">🗑</button>
                    </div>
                `;
                item.onclick = () => this.loadSession(s.session_id);
                list.appendChild(item);
            });
        } catch (e) {
            console.warn('Failed to load sessions:', e);
        }
    },

    async loadSession(sessionId) {
        try {
            const qs = this._ownerQS();
            const resp = await fetch(`/api/sessions/${sessionId}${qs ? '?' + qs : ''}`);
            const data = await resp.json();

            this.sessionId = sessionId;
            localStorage.setItem('cc_session_id', sessionId);
            document.getElementById('chat-title').textContent = data.title || 'Chat';

            const container = document.getElementById('messages');
            container.innerHTML = '';

            (data.messages || []).forEach(msg => {
                if (msg.role === 'user') {
                    this._addMessage('user', msg.content);
                } else {
                    // Try to parse as JSON blocks
                    try {
                        const blocks = JSON.parse(msg.content);
                        if (Array.isArray(blocks)) {
                            this._addRichMessage(blocks);
                        } else {
                            this._addMessage('assistant', msg.content);
                        }
                    } catch {
                        this._addMessage('assistant', msg.content);
                    }
                }
            });

            this.loadSessions(); // Refresh active indicator
        } catch (e) {
            console.warn('Failed to load session:', e);
        }
    },

    async togglePinSession(sessionId) {
        try {
            const qs = this._ownerQS();
            await fetch(`/api/sessions/${sessionId}/pin${qs ? '?' + qs : ''}`, { method: 'POST' });
            this.loadSessions();
        } catch (e) {
            console.error('Failed to toggle pin:', e);
        }
    },

    async deleteSession(sessionId) {
        try {
            const qs = this._ownerQS();
            await fetch(`/api/sessions/${sessionId}${qs ? '?' + qs : ''}`, { method: 'DELETE' });
            if (this.sessionId === sessionId) {
                this.sessionId = null;
                localStorage.removeItem('cc_session_id');
                document.getElementById('messages').innerHTML = '';
                document.getElementById('chat-title').textContent = 'New Chat';
            }
            this.loadSessions();
        } catch (e) {
            console.error('Failed to delete session:', e);
        }
    },

    async clearAllSessions() {
        if (!confirm('Delete ALL sessions? This cannot be undone.')) return;
        try {
            const qsClear = this._ownerQS();
            await fetch(`/api/sessions/clear${qsClear ? '?' + qsClear : ''}`, { method: 'POST' });
            this.sessionId = null;
            localStorage.removeItem('cc_session_id');
            document.getElementById('messages').innerHTML = '';
            document.getElementById('chat-title').textContent = 'New Chat';
            this.loadSessions();
        } catch (e) {
            console.error('Failed to clear sessions:', e);
        }
    },

    async loadPlugins() {
        try {
            const resp = await fetch('/api/plugins');
            const plugins = await resp.json();
            const list = document.getElementById('plugin-list');
            list.innerHTML = '';

            plugins.forEach(p => {
                const item = document.createElement('div');
                item.className = 'cc-plugin-item';
                item.innerHTML = `
                    <span>${p.name}</span>
                    <div class="cc-plugin-toggle ${p.enabled ? 'active' : ''}"
                         onclick="CC.togglePlugin('${p.id}', ${!p.enabled})"></div>
                `;
                list.appendChild(item);
            });
        } catch (e) {
            // Plugins not yet available
        }
    },

    async togglePlugin(pluginId, enable) {
        try {
            await fetch(`/api/plugins/${pluginId}/${enable ? 'enable' : 'disable'}`, { method: 'POST' });
            this.loadPlugins();
        } catch (e) {
            console.error('Plugin toggle failed:', e);
        }
    },

    // ─── File Upload ────────────────────────────────────────────────

    _initUpload() {
        const attachBtn = document.getElementById('btn-attach');
        const fileInput = document.getElementById('file-input');
        const chatMain = document.querySelector('main');

        // Paperclip button → open file picker
        attachBtn.addEventListener('click', () => {
            if (!this.isStreaming) fileInput.click();
        });

        // File input change
        fileInput.addEventListener('change', () => {
            if (fileInput.files.length > 0) {
                this._handleFiles(Array.from(fileInput.files));
                fileInput.value = ''; // Reset so same file can be re-selected
            }
        });

        // Paste handler — catch pasted files
        document.getElementById('user-input').addEventListener('paste', (e) => {
            const files = [];
            if (e.clipboardData?.files) {
                for (const f of e.clipboardData.files) files.push(f);
            }
            if (files.length > 0) {
                e.preventDefault();
                this._handleFiles(files);
            }
        });

        // Drag & drop
        if (chatMain) {
            let dragCounter = 0;
            const overlay = document.getElementById('drop-overlay');

            chatMain.style.position = 'relative';

            chatMain.addEventListener('dragenter', (e) => {
                e.preventDefault();
                e.stopPropagation();
                dragCounter++;
                if (dragCounter === 1 && !this.isStreaming) {
                    overlay.style.display = 'flex';
                }
            });

            chatMain.addEventListener('dragleave', (e) => {
                e.preventDefault();
                e.stopPropagation();
                dragCounter--;
                if (dragCounter <= 0) {
                    dragCounter = 0;
                    overlay.style.display = 'none';
                }
            });

            chatMain.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.stopPropagation();
            });

            chatMain.addEventListener('drop', (e) => {
                e.preventDefault();
                e.stopPropagation();
                dragCounter = 0;
                overlay.style.display = 'none';
                if (!this.isStreaming) {
                    const files = [];
                    if (e.dataTransfer?.files) {
                        for (const f of e.dataTransfer.files) files.push(f);
                    }
                    if (files.length > 0) this._handleFiles(files);
                }
            });
        }
    },

    async _handleFiles(files) {
        const attachBtn = document.getElementById('btn-attach');
        attachBtn.classList.add('uploading');

        try {
            const formData = new FormData();
            for (const f of files) {
                formData.append('files', f);
            }
            if (this.sessionId) {
                formData.append('session_id', this.sessionId);
            }
            // Stamp uploader so cross-user file access can be blocked.
            const uctx = this._userCtx || {};
            if (uctx.user_id !== undefined && uctx.user_id !== null) {
                formData.append('user_id', String(uctx.user_id));
            }
            if (uctx.tenant_id !== undefined && uctx.tenant_id !== null) {
                formData.append('tenant_id', String(uctx.tenant_id));
            }

            const resp = await fetch('/api/upload', {
                method: 'POST',
                body: formData,
            });

            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || `Upload failed (${resp.status})`);
            }

            const result = await resp.json();
            if (result.files) {
                for (const fileMeta of result.files) {
                    this._stagedFiles.push(fileMeta);
                }
                this._renderStagedFiles();
            }
        } catch (err) {
            console.error('File upload failed:', err);
            alert(`Upload failed: ${err.message}`);
        } finally {
            attachBtn.classList.remove('uploading');
        }
    },

    _renderStagedFiles() {
        const container = document.getElementById('staged-files');
        if (this._stagedFiles.length === 0) {
            container.style.display = 'none';
            container.innerHTML = '';
            return;
        }

        container.style.display = 'flex';
        container.innerHTML = '';

        for (const file of this._stagedFiles) {
            const chip = document.createElement('div');
            chip.className = 'cc-file-chip';

            const name = file.filename.length > 24
                ? file.filename.substring(0, 21) + '...'
                : file.filename;

            const sizeKb = file.size / 1024;
            const sizeStr = sizeKb >= 1024
                ? `${(sizeKb / 1024).toFixed(1)} MB`
                : `${sizeKb.toFixed(0)} KB`;

            chip.innerHTML = `
                <span class="cc-file-chip-name" title="${file.filename}">${name}</span>
                <span class="cc-file-chip-size">${sizeStr}</span>
                <button class="cc-file-chip-remove" data-file-id="${file.file_id}" title="Remove">✕</button>
            `;

            chip.querySelector('.cc-file-chip-remove').addEventListener('click', (e) => {
                this._removeFile(e.currentTarget.dataset.fileId);
            });

            container.appendChild(chip);
        }
    },

    async _removeFile(fileId) {
        this._stagedFiles = this._stagedFiles.filter(f => f.file_id !== fileId);
        this._renderStagedFiles();

        // Delete from server
        try {
            await fetch(`/api/uploads/${fileId}`, { method: 'DELETE' });
        } catch (err) {
            console.error('Failed to delete file:', err);
        }
    },

    _formatFileSize(bytes) {
        if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MB`;
        if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
        return `${bytes} B`;
    },
};

// Initialize on load
document.addEventListener('DOMContentLoaded', () => CC.init());
