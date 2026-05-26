/**
 * builder.js
 *
 * Top-level controller for the Schema Builder Wizard.
 *
 * Owns:
 *   - The session_id with the backend builder agent
 *   - The chat panel (left pane)
 *   - The SchemaEditor (right pane)
 *   - Save / Test Drive flow
 *
 * Sync model: the schema is "owned" by the backend (the builder agent's in-memory
 * dict). Every chat round-trip returns the latest schema in metadata.schema, and
 * we re-render the editor from that. When the user edits the form directly, we
 * push the updated schema to the backend via PUT /schema and re-render from the
 * response. This keeps the chat agent and the form editor in lockstep.
 */

class SchemaBuilderApp {
    constructor(boot) {
        this.mode = boot.mode;                 // 'new' | 'edit'
        this.configId = boot.config_id || '';
        this.initialSchema = boot.initial_schema;

        this.sessionId = this._uuid();
        this.schema = this.initialSchema || null;
        this.workflows = [];
        this.agents = [];
        this.connections = [];
        this.customTools = [];

        // DOM refs
        this.$messages = document.getElementById('dcaBuilderMessages');
        this.$input = document.getElementById('dcaBuilderInput');
        this.$sendBtn = document.getElementById('dcaBuilderSendBtn');
        this.$saveBtn = document.getElementById('dcaSaveBtn');
        this.$testDriveBtn = document.getElementById('dcaTestDriveBtn');
        this.$schemaPicker = document.getElementById('dcaSchemaPicker');
        this.$themeBtn = document.getElementById('dcaBuilderThemeBtn');
        this.$phaseLabel = document.getElementById('dcaBuilderPhaseLabel');
        this.$validationSummary = document.getElementById('dcaBuilderValidationSummary');
        this.$builderTitle = document.getElementById('dcaBuilderTitle');
    }

    // ------------------------------------------------------------------
    async init() {
        this.editor = new SchemaEditor(document.getElementById('dcaSchemaEditor'), {
            onChange: (newSchema) => this._onEditorChange(newSchema),
        });

        // Wire static handlers
        this.$sendBtn.addEventListener('click', () => this.sendMessage());
        this.$input.addEventListener('keydown', e => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.sendMessage(); }
        });
        this.$saveBtn.addEventListener('click', () => this.save());
        this.$testDriveBtn.addEventListener('click', () => this.testDrive());
        this.$themeBtn.addEventListener('click', () => this._toggleTheme());
        if (this.$schemaPicker) {
            this.$schemaPicker.addEventListener('change', () => {
                const id = this.$schemaPicker.value;
                if (id) window.location.href = `/data-collection/builder/${encodeURIComponent(id)}`;
            });
        }

        // Load pickers (workflows / agents / DB connections / custom tools)
        // for the action editor, lookup modal, and custom-tools panel.
        await Promise.all([
            this._loadWorkflows(),
            this._loadAgents(),
            this._loadConnections(),
            this._loadCustomTools(),
            this._loadSavedSchemas(),
        ]);
        this.editor.setPickers({
            workflows: this.workflows,
            agents: this.agents,
            connections: this.connections,
            customTools: this.customTools,
        });

        // Initial schema state
        if (!this.schema) {
            const resp = await this._api('/api/data-collection/builder/empty-schema');
            this.schema = resp.schema;
            if (this.configId) this.schema.id = this.configId;
        }

        this._renderEditor();
        this._updateTitle();

        // Send a synthetic kickoff message so the agent greets the user
        const greeting = this.mode === 'edit'
            ? `I'm editing the schema "${this.schema.name || this.schema.id}". Please greet me and ask what I'd like to change.`
            : `I'm starting a new schema. Please greet me and ask what kind of data I want to collect.`;
        await this.sendMessage(greeting, /* visible */ false);
    }

    // ------------------------------------------------------------------
    // Chat
    // ------------------------------------------------------------------
    async sendMessage(textOverride = null, isVisible = true) {
        const text = textOverride !== null ? textOverride : (this.$input.value || '').trim();
        if (!text) return;

        if (isVisible) {
            this._appendMsg('user', text);
            this.$input.value = '';
        }
        const $typing = this._appendTyping();
        this._setComposerEnabled(false);

        try {
            const resp = await this._api('/api/data-collection/builder/message', 'POST', {
                session_id: this.sessionId,
                message: text,
                initial_schema: this.schema,
            });
            $typing.remove();
            const md = resp.metadata || {};
            this._appendMsg('assistant', resp.response || '');
            // Adopt the agent's schema state (it may have called tools that mutated it)
            if (md.schema) {
                this.schema = md.schema;
                this._renderEditor();
                this._updateTitle();
            }
            if (md.phase) this.$phaseLabel.textContent = md.phase;
            this._updateValidationSummary(md.validation);
        } catch (err) {
            $typing.remove();
            this._appendMsg('assistant', `Error: ${err.message || err}`);
        } finally {
            this._setComposerEnabled(true);
            this.$input.focus();
        }
    }

    // ------------------------------------------------------------------
    // Editor → backend sync
    // ------------------------------------------------------------------
    async _onEditorChange(newSchema) {
        this.schema = newSchema;
        this._updateTitle();
        try {
            const resp = await this._api(
                `/api/data-collection/builder/session/${this.sessionId}/schema`,
                'PUT',
                { schema: newSchema },
            );
            if (resp.schema) {
                // Backend may normalize the schema; adopt its version
                this.schema = resp.schema;
            }
            this._updateValidationSummary(resp.validation);
            // Re-render the editor with the canonical state (preserves expand/collapse)
            this.editor.render(this.schema, resp.validation);
        } catch (err) {
            console.warn('[builder] Failed to sync schema:', err);
        }
    }

    _renderEditor() {
        this.editor.render(this.schema, null);
        // Kick off a fresh validation
        this._validateRemote();
    }

    async _validateRemote() {
        try {
            const resp = await this._api('/api/data-collection/builder/validate', 'POST', {
                schema: this.schema,
                is_new: this.mode === 'new',
            });
            this._updateValidationSummary(resp.result);
            this.editor.render(this.schema, resp.result);
        } catch (err) {
            console.warn('[builder] validate failed:', err);
        }
    }

    _updateValidationSummary(result) {
        if (!result) return;
        const errs = (result.errors || []).length;
        const warns = (result.warnings || []).length;
        let txt;
        if (errs === 0 && warns === 0) {
            txt = 'no issues';
            this.$validationSummary.classList.remove('has-errors', 'has-warnings');
        } else {
            txt = `${errs} error${errs !== 1 ? 's' : ''}, ${warns} warning${warns !== 1 ? 's' : ''}`;
            this.$validationSummary.classList.toggle('has-errors', errs > 0);
            this.$validationSummary.classList.toggle('has-warnings', warns > 0 && errs === 0);
        }
        this.$validationSummary.textContent = txt;
    }

    // ------------------------------------------------------------------
    // Save / Test Drive
    // ------------------------------------------------------------------
    async save() {
        try {
            const resp = await this._api('/api/data-collection/builder/save', 'POST', {
                schema: this.schema,
                is_new: this.mode === 'new',
            });
            if (resp.status === 'success') {
                this._appendMsg('system', `Saved schema "${resp.config_id}".`);
                this._updateValidationSummary(resp.result);
                this.mode = 'edit';     // Subsequent saves are updates
                this.configId = resp.config_id;
            }
        } catch (err) {
            const msg = (err.payload && err.payload.result && err.payload.result.errors)
                ? err.payload.result.errors.map(e => `• ${e.path}: ${e.message}`).join('\n')
                : (err.message || String(err));
            this._appendMsg('system', `Save failed:\n${msg}`);
            // Surface validation in the editor too
            if (err.payload && err.payload.result) {
                this._updateValidationSummary(err.payload.result);
                this.editor.render(this.schema, err.payload.result);
            }
        }
    }

    async testDrive() {
        // Save first (so the runtime can load it)
        await this.save();
        if (this.configId) {
            window.open(`/data-collection/${encodeURIComponent(this.configId)}`, '_blank');
        }
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------
    async _loadWorkflows() {
        try {
            const resp = await this._api('/api/data-collection/builder/workflows');
            this.workflows = resp.workflows || [];
        } catch (_) { this.workflows = []; }
    }

    async _loadAgents() {
        try {
            const resp = await this._api('/api/data-collection/builder/agents');
            this.agents = resp.agents || [];
        } catch (_) { this.agents = []; }
    }

    async _loadConnections() {
        try {
            const resp = await this._api('/api/data-collection/builder/connections');
            this.connections = resp.connections || [];
        } catch (_) { this.connections = []; }
    }

    async _loadCustomTools() {
        try {
            const resp = await this._api('/api/data-collection/builder/custom-tools');
            this.customTools = resp.tools || [];
        } catch (_) { this.customTools = []; }
    }

    async _loadSavedSchemas() {
        if (!this.$schemaPicker) return;
        try {
            const resp = await this._api('/api/data-collection/builder/list');
            const schemas = resp.schemas || [];
            // Preserve the placeholder option, then append a sorted list
            this.$schemaPicker.innerHTML = '<option value="">— saved schemas —</option>'
                + schemas
                    .slice()
                    .sort((a, b) => (a.name || a.id || '').localeCompare(b.name || b.id || ''))
                    .map(s => `<option value="${this._escAttr(s.id)}"
                                       ${this.configId === s.id ? 'selected' : ''}>
                                    ${this._escAttr(s.name || s.id)} (${this._escAttr(s.id)})
                               </option>`).join('');
        } catch (_) { /* picker just stays empty */ }
    }

    _escAttr(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/"/g, '&quot;')
            .replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    _appendMsg(role, content) {
        const row = document.createElement('div');
        row.className = `dca-msg-row ${role}`;
        const bubble = document.createElement('div');
        bubble.className = 'dca-msg-bubble';
        bubble.innerHTML = this._textToHtml(content);
        row.appendChild(bubble);
        this.$messages.appendChild(row);
        this.$messages.scrollTop = this.$messages.scrollHeight;
    }

    _appendTyping() {
        const row = document.createElement('div');
        row.className = 'dca-msg-row assistant';
        row.innerHTML = `<div class="dca-typing">
            <span class="dca-typing-dot"></span>
            <span class="dca-typing-dot"></span>
            <span class="dca-typing-dot"></span>
        </div>`;
        this.$messages.appendChild(row);
        this.$messages.scrollTop = this.$messages.scrollHeight;
        return row;
    }

    _setComposerEnabled(on) {
        this.$input.disabled = !on;
        this.$sendBtn.disabled = !on;
    }

    _textToHtml(text) {
        if (!text) return '';
        const escaped = (text || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
        return escaped
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\n/g, '<br>');
    }

    _updateTitle() {
        const name = (this.schema && this.schema.name) || this.configId || 'Untitled schema';
        const id = (this.schema && this.schema.id) || '';
        this.$builderTitle.textContent = name + (id ? ` — ${id}` : '');
    }

    _toggleTheme() {
        document.body.classList.toggle('light-mode');
        const icon = this.$themeBtn.querySelector('i');
        if (icon) icon.className = document.body.classList.contains('light-mode') ? 'fas fa-sun' : 'fas fa-moon';
    }

    async _api(url, method = 'GET', body = null) {
        const opts = {
            method,
            headers: { 'Accept': 'application/json' },
            credentials: 'same-origin',
        };
        if (body !== null) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        }
        const resp = await fetch(url, opts);
        let payload = null;
        try { payload = await resp.json(); } catch (_) { payload = { status: 'error', error: `HTTP ${resp.status}` }; }
        if (!resp.ok && payload && payload.status === 'error') {
            const err = new Error(payload.error || `HTTP ${resp.status}`);
            err.payload = payload;
            throw err;
        }
        return payload;
    }

    _uuid() {
        return 'dca-' + 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
            const r = Math.random() * 16 | 0;
            const v = c === 'x' ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
    }
}

window.SchemaBuilderApp = SchemaBuilderApp;
