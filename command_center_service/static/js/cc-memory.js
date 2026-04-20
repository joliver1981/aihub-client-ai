/**
 * Command Center — Memory & Suggestions
 * Loads user suggestions (from Route Memory) and manages the suggestion chip UI.
 */

const CCMemory = {
    userId: null,
    _suggestions: [],  // cache for delete operations
    _currentTab: 'preferences',
    _managerPreferences: {},
    _managerRoutes: [],
    _managerDirty: false,  // track if deletions happened

    init(userId) {
        this.userId = userId;
        if (userId) {
            this.loadSuggestions();
        } else {
            this.renderSuggestions([]);
        }
    },

    async loadSuggestions() {
        if (!this.userId) return;

        try {
            const resp = await fetch(`/api/memory/suggestions?user_id=${this.userId}&limit=5`);
            if (!resp.ok) {
                // Transient server error — don't invent fake history. Show empty.
                this._suggestions = [];
                this.renderSuggestions([]);
                return;
            }

            // Suggestions come ONLY from the user's own route-memory history.
            // Empty array → empty list (the renderer shows a "No suggestions
            // yet" empty state). No hardcoded starters.
            const suggestions = await resp.json();
            this._suggestions = Array.isArray(suggestions) ? suggestions : [];
            this.renderSuggestions(this._suggestions);
        } catch (e) {
            console.warn('Failed to load suggestions:', e);
            this._suggestions = [];
            this.renderSuggestions([]);
        }
    },

    async deleteSuggestion(routeId) {
        if (!this.userId) return;
        try {
            await fetch(`/api/memory/suggestions/${encodeURIComponent(routeId)}?user_id=${this.userId}`, {
                method: 'DELETE'
            });
            this.loadSuggestions();
        } catch (e) {
            console.warn('Failed to delete suggestion:', e);
        }
    },

    async clearAllSuggestions() {
        if (this.userId) {
            try {
                await fetch(`/api/memory/suggestions?user_id=${this.userId}`, { method: 'DELETE' });
            } catch (e) {
                console.warn('Failed to clear suggestions:', e);
            }
        }
        this._suggestions = [];
        this.renderSuggestions([]);
    },

    renderSuggestions(suggestions) {
        const container = document.getElementById('suggestion-chips');
        if (!container) return;

        const clearAllBtn = document.getElementById('suggestions-clear-all');
        if (clearAllBtn) {
            clearAllBtn.style.display = suggestions && suggestions.length > 0 ? 'inline-block' : 'none';
        }

        container.innerHTML = '';

        if (!suggestions || suggestions.length === 0) {
            container.innerHTML = '<div style="padding:4px 0;font-size:12px;color:var(--cc-text-muted)">No suggestions yet</div>';
            return;
        }

        suggestions.forEach(s => {
            const wrapper = document.createElement('div');
            wrapper.className = 'cc-chip-wrapper';

            const chip = document.createElement('button');
            chip.className = 'cc-chip';

            // Display normalized_query as label, or truncated prompt
            const displayText = s.normalized_query || (s.prompt.length > 35 ? s.prompt.substring(0, 32) + '\u2026' : s.prompt);
            chip.textContent = displayText;

            const desc = s.description || '';
            chip.title = `${s.prompt}\n${desc}`;

            if (s.success_rate !== undefined && s.success_rate < 0.5) {
                chip.style.opacity = '0.7';
                chip.title += '\n\u26A0\uFE0F This query sometimes fails';
            }

            chip.onclick = () => {
                document.getElementById('user-input').value = s.prompt;
                CC.send();
            };

            wrapper.appendChild(chip);

            // Delete button — every suggestion comes from the user's own
            // route-memory history, so all are removable.
            const delBtn = document.createElement('button');
            delBtn.className = 'cc-chip-delete';
            delBtn.textContent = '\u2715';
            delBtn.title = 'Remove suggestion';
            delBtn.onclick = (e) => {
                e.stopPropagation();
                this.deleteSuggestion(s.route_id || s.prompt);
            };
            wrapper.appendChild(delBtn);

            container.appendChild(wrapper);
        });
    },

    /* ── Memory Manager Modal ── */

    openManager() {
        this._currentTab = 'preferences';
        this._managerDirty = false;
        document.getElementById('memory-modal').style.display = 'flex';
        document.querySelectorAll('.cc-modal-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === 'preferences'));
        this._loadManagerData();
    },

    closeManager() {
        document.getElementById('memory-modal').style.display = 'none';
        if (this._managerDirty) {
            this.loadSuggestions();
        }
    },

    switchTab(tab) {
        this._currentTab = tab;
        document.querySelectorAll('.cc-modal-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
        this._renderCurrentTab();
    },

    async _loadManagerData() {
        const body = document.getElementById('memory-modal-body');
        body.innerHTML = '<div class="cc-memory-loading">Loading...</div>';

        try {
            const [prefsResp, routesResp] = await Promise.all([
                fetch(`/api/memory/preferences?user_id=${this.userId}`),
                fetch(`/api/memory/routes?user_id=${this.userId}&limit=50`),
            ]);
            this._managerPreferences = prefsResp.ok ? await prefsResp.json() : {};
            this._managerRoutes = routesResp.ok ? await routesResp.json() : [];
        } catch (e) {
            console.warn('Failed to load manager data:', e);
            this._managerPreferences = {};
            this._managerRoutes = [];
        }
        this._renderCurrentTab();
    },

    _renderCurrentTab() {
        if (this._currentTab === 'preferences') {
            this._renderPreferencesTab();
        } else {
            this._renderRoutesTab();
        }
    },

    _extractDisplayValue(raw) {
        if (typeof raw === 'string') return raw;
        if (raw === null || raw === undefined) return '';
        if (typeof raw !== 'object') return String(raw);
        if ('value' in raw) return String(raw.value);
        if (raw.agent_name) {
            const id = raw.agent_id ? ` (agent_id: ${raw.agent_id})` : '';
            return `${raw.agent_name}${id}`;
        }
        return JSON.stringify(raw);
    },

    _renderPreferencesTab() {
        const body = document.getElementById('memory-modal-body');
        const prefs = this._managerPreferences;
        const keys = Object.keys(prefs);

        if (keys.length === 0) {
            body.innerHTML = '<div class="cc-memory-empty">No preferences saved yet.<br><span>Tell the agent to &ldquo;remember&rdquo; something to create a preference.</span></div>';
            return;
        }

        body.innerHTML = '';
        keys.forEach(key => {
            const displayValue = this._extractDisplayValue(prefs[key]);
            const label = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

            const row = document.createElement('div');
            row.className = 'cc-memory-row';
            row.dataset.key = key;

            const keyEl = document.createElement('div');
            keyEl.className = 'cc-memory-key';
            keyEl.title = key;
            keyEl.textContent = label;

            const input = document.createElement('input');
            input.className = 'cc-memory-value-input';
            input.value = displayValue;
            input.dataset.original = displayValue;
            input.addEventListener('change', () => CCMemory._onPrefChange(input));
            input.addEventListener('input', () => CCMemory._onPrefChange(input));

            const saveBtn = document.createElement('button');
            saveBtn.className = 'cc-memory-btn cc-memory-btn-save';
            saveBtn.style.display = 'none';
            saveBtn.innerHTML = '&#x2713;';
            saveBtn.title = 'Save';
            saveBtn.addEventListener('click', () => CCMemory._savePreference(key, saveBtn));

            const delBtn = document.createElement('button');
            delBtn.className = 'cc-memory-btn cc-memory-btn-delete';
            delBtn.innerHTML = '&#x2715;';
            delBtn.title = 'Delete';
            delBtn.addEventListener('click', () => CCMemory._deletePreference(key));

            row.append(keyEl, input, saveBtn, delBtn);
            body.appendChild(row);
        });
    },

    _renderRoutesTab() {
        const body = document.getElementById('memory-modal-body');
        const routes = this._managerRoutes;

        if (!routes || routes.length === 0) {
            body.innerHTML = '<div class="cc-memory-empty">No learned routes yet.<br><span>Routes are learned automatically as you use the Command Center.</span></div>';
            return;
        }

        body.innerHTML = '';
        routes.forEach(r => {
            const nq = r.normalized_query || '(unclassified)';
            const successPct = r.success_rate !== undefined ? Math.round(r.success_rate * 100) : 100;

            const row = document.createElement('div');
            row.className = 'cc-memory-row';
            if (successPct < 50) {
                row.classList.add('cc-memory-low-success');
            }

            const info = document.createElement('div');
            info.className = 'cc-memory-pattern-info';

            const labelEl = document.createElement('div');
            labelEl.className = 'cc-memory-pattern-label';
            labelEl.textContent = nq;

            const descEl = document.createElement('div');
            descEl.className = 'cc-memory-pattern-desc';
            descEl.textContent = `${r.agent_name || 'Unknown agent'} | ${r.intent || ''} | ${r.usage_count || 0}x used | ${successPct}% success`;

            // Show sample queries
            const samplesEl = document.createElement('div');
            samplesEl.className = 'cc-memory-pattern-prompt';
            const samples = r.sample_queries || [];
            samplesEl.textContent = samples.length > 0 ? samples[0] : '';
            if (samples.length > 1) {
                samplesEl.title = samples.join('\n');
            }

            info.append(labelEl, descEl, samplesEl);

            const delBtn = document.createElement('button');
            delBtn.className = 'cc-memory-btn cc-memory-btn-delete';
            delBtn.innerHTML = '&#x2715;';
            delBtn.title = 'Delete this learned route';
            delBtn.addEventListener('click', () => CCMemory._deleteRouteGroup(nq));

            row.append(info, delBtn);
            body.appendChild(row);
        });
    },

    _onPrefChange(input) {
        const row = input.closest('.cc-memory-row');
        const saveBtn = row.querySelector('.cc-memory-btn-save');
        saveBtn.style.display = input.value !== input.dataset.original ? 'inline-flex' : 'none';
    },

    async _savePreference(key, btn) {
        const row = btn.closest('.cc-memory-row');
        const input = row.querySelector('.cc-memory-value-input');
        const value = input.value;

        try {
            await fetch(`/api/memory/preferences?user_id=${this.userId}&key=${encodeURIComponent(key)}&value=${encodeURIComponent(value)}`, { method: 'PUT' });
            input.dataset.original = value;
            btn.style.display = 'none';
            row.classList.add('cc-memory-row-saved');
            setTimeout(() => row.classList.remove('cc-memory-row-saved'), 800);
        } catch (e) {
            console.warn('Failed to save preference:', e);
        }
    },

    async _deletePreference(key) {
        try {
            await fetch(`/api/memory/preferences/${encodeURIComponent(key)}?user_id=${this.userId}`, { method: 'DELETE' });
            delete this._managerPreferences[key];
            this._renderPreferencesTab();
        } catch (e) {
            console.warn('Failed to delete preference:', e);
        }
    },

    async _deleteRouteGroup(normalizedQuery) {
        try {
            await fetch(`/api/memory/routes/canonical?user_id=${this.userId}&normalized_query=${encodeURIComponent(normalizedQuery)}`, { method: 'DELETE' });
            this._managerRoutes = this._managerRoutes.filter(r => r.normalized_query !== normalizedQuery);
            this._managerDirty = true;
            this._renderRoutesTab();
        } catch (e) {
            console.warn('Failed to delete route group:', e);
        }
    },

    async clearAllCurrentTab() {
        const type = this._currentTab === 'preferences' ? 'preferences' : 'learned routes';
        if (!confirm(`Clear all ${type}? This cannot be undone.`)) return;

        try {
            let resp;
            if (this._currentTab === 'preferences') {
                resp = await fetch(`/api/memory/preferences?user_id=${this.userId}`, { method: 'DELETE' });
            } else {
                resp = await fetch(`/api/memory/routes?user_id=${this.userId}`, { method: 'DELETE' });
            }
            if (!resp.ok) {
                console.warn('Clear all response not OK:', resp.status, await resp.text().catch(() => ''));
            }
            this._managerDirty = true;
            await this._loadManagerData();
        } catch (e) {
            console.warn('Failed to clear all:', e);
        }
    },
};
