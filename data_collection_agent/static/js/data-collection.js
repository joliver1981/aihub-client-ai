/**
 * data-collection.js
 *
 * Main controller for the Data Collection Agent runtime page.
 *
 * Responsibilities:
 *   - Bootstraps the page (load schema, decide new vs. resume, render initial state)
 *   - Owns the conversation: send messages to /api/data-collection/message,
 *     render assistant replies (text + rich content blocks), persist locally
 *   - Keeps the right-hand ProgressPanel in sync with the latest server state
 *   - Handles the back/edit flows (section navigation + inline field edits)
 *   - Drives the submission overlay when the user submits
 */

class DataCollectionApp {
    constructor(configId) {
        this.configId = configId;
        this.sessionId = null;
        this.schema = null;
        this.lastMetadata = null;          // The most recent metadata dict from /message
        this.progressPanel = null;
        this.renderer = null;

        // DOM refs
        this.$messages = document.getElementById('dcaMessages');
        this.$input = document.getElementById('dcaInput');
        this.$sendBtn = document.getElementById('dcaSendBtn');
        this.$status = document.getElementById('dcaStatus');
        this.$progressFill = document.getElementById('dcaProgressFill');
        this.$progressLabel = document.getElementById('dcaProgressLabel');
        this.$progressList = document.getElementById('dcaProgressList');
        this.$sectionCounter = document.getElementById('dcaSectionCounter');
        this.$submitBanner = document.getElementById('dcaSubmitBanner');
        this.$submitBtn = document.getElementById('dcaSubmitBtn');
        this.$resumeBtn = document.getElementById('dcaResumeBtn');
        this.$newSessionBtn = document.getElementById('dcaNewSessionBtn');
        this.$resetBtn = document.getElementById('dcaResetBtn');
        this.$themeBtn = document.getElementById('dcaThemeBtn');

        // Voice mode (Phase 2)
        this.$voiceToggle = document.getElementById('dcaVoiceToggle');
        this.$muteToggle = document.getElementById('dcaMuteToggle');
        this.$voiceComposer = document.getElementById('dcaVoiceComposer');
        this.$textComposer = document.getElementById('dcaTextComposer');
        this.$voiceMic = document.getElementById('dcaVoiceMic');
        this.$voiceState = document.getElementById('dcaVoiceState');
        this.$voiceCancel = document.getElementById('dcaVoiceCancel');
        this.voice = null;  // VoiceModeController, lazy-created on first activation

        this.$inlineModal = document.getElementById('dcaInlineEditModal');
        this.$inlineTitle = document.getElementById('dcaInlineEditTitle');
        this.$inlineBody = document.getElementById('dcaInlineEditBody');
        this.$inlineSave = document.getElementById('dcaInlineEditSave');
        this.$inlineCancel = document.getElementById('dcaInlineEditCancel');
        this.$inlineClose = document.getElementById('dcaInlineEditClose');

        this.$submissionOverlay = document.getElementById('dcaSubmissionOverlay');
        this.$submissionActions = document.getElementById('dcaSubmissionActions');
        this.$submissionMessage = document.getElementById('dcaSubmissionMessage');
        this.$submissionDone = document.getElementById('dcaSubmissionDone');

        this._inlineEditCtx = null;        // {sectionId, fieldId, fieldDef}
    }

    // -------------------------------------------------------------------
    async init() {
        // Defensively flush any pending browser TTS utterances left over
        // from a previous page-load. Chrome and Edge keep the
        // window.speechSynthesis queue alive across navigations / reloads,
        // so utterances that didn't finish in the last tab can start
        // playing when this page mounts — with the robotic browser voice
        // (not our nice OpenAI TTS one), and with content from a prior
        // conversation. cancel() drains that queue immediately.
        try {
            if (typeof window !== 'undefined' && window.speechSynthesis) {
                window.speechSynthesis.cancel();
            }
        } catch (_) { /* non-fatal */ }

        // Wire static event handlers
        this.$sendBtn.addEventListener('click', () => this.sendMessage());
        this.$input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });
        this.$submitBtn.addEventListener('click', () => this.submit());
        this.$newSessionBtn.addEventListener('click', () => this.startNewSession());
        this.$resumeBtn.addEventListener('click', () => this.resumeMostRecent());
        this.$resetBtn.addEventListener('click', () => this.startNewSession(true));
        this.$themeBtn.addEventListener('click', () => this.toggleTheme());

        this.$inlineCancel.addEventListener('click', () => this._closeInline());
        this.$inlineClose.addEventListener('click', () => this._closeInline());
        this.$inlineSave.addEventListener('click', () => this._saveInline());
        this.$submissionDone.addEventListener('click', () => this._closeSubmissionOverlay());

        // Mobile drawer toggles (visible only on small screens via CSS)
        this._wireDrawers();

        // Event delegation for recap_panel edit buttons. The buttons live
        // inside chat bubbles (rendered by DcaRichRenderer), so we listen
        // at the messages container level and find a field def on click.
        if (this.$messages) {
            this.$messages.addEventListener('click', (e) => {
                const btn = e.target && e.target.closest && e.target.closest('.dca-rp-edit-btn');
                if (!btn) return;
                const sectionId = btn.dataset.sectionId;
                const fieldId = btn.dataset.fieldId;
                if (!sectionId || !fieldId) return;
                const fieldDef = this._findFieldDef(sectionId, fieldId);
                if (!fieldDef) return;
                this._openInline(sectionId, fieldId, fieldDef);
            });
        }

        // Voice mode toggle (creates the controller lazily). When the page
        // isn't a secure context, dim the button + change the tooltip so
        // users see the constraint before they click. Click still routes
        // through _toggleVoiceMode for the inline notice.
        if (this.$voiceToggle) {
            this.$voiceToggle.addEventListener('click', () => this._toggleVoiceMode());
            if (!window.isSecureContext) {
                this.$voiceToggle.classList.add('dca-voice-unavailable');
                this.$voiceToggle.setAttribute(
                    'title',
                    'Voice mode requires HTTPS. Click for details.'
                );
            }
        }
        if (this.$muteToggle) {
            this.$muteToggle.addEventListener('click', () => {
                if (this.voice) this.voice.setMuted(!this.voice.muted);
            });
        }
        if (this.$voiceCancel) {
            this.$voiceCancel.addEventListener('click', () => {
                if (this.voice) this.voice.deactivate();
            });
        }

        // Renderer initialization. We do NOT gate on window.RichContentRenderer
        // because top-level `class` declarations in the platform's
        // richContentRenderer.js may not auto-attach to window under SES
        // / hardened-JS environments (MetaMask et al). DcaRichRenderer
        // handles every block type DCA emits (info_card, comparison,
        // field_help, tip_callout, recap_panel, table) on its own — it
        // only delegates to the platform for unknown types like
        // 'chart' / 'code' / 'metrics' / 'image' that DCA doesn't use.
        // So our renderer must be initialized regardless.
        const platformRenderer = (typeof window.RichContentRenderer === 'function')
            ? new window.RichContentRenderer()
            : null;
        if (typeof window.DcaRichRenderer === 'function') {
            this.renderer = new window.DcaRichRenderer(platformRenderer);
            console.log('[dca] DcaRichRenderer initialized'
                + (platformRenderer ? ' (with platform fallback)' : ' (no platform fallback)'));
        } else if (platformRenderer) {
            this.renderer = platformRenderer;
            console.warn('[dca] DcaRichRenderer not loaded — using platform renderer only.');
        } else {
            console.error('[dca] NO renderer available — neither DcaRichRenderer nor platform '
                + 'RichContentRenderer is on window. Rich content will not render. '
                + 'Check script load order in data_collection.html.');
        }

        // Load schema
        this.setStatus('Loading schema…');
        try {
            const schemaResp = await this.api(`/api/data-collection/schema/${this.configId}`);
            this.schema = schemaResp.schema;
        } catch (err) {
            this.setStatus('Failed to load schema');
            this._appendMsg('assistant', `Error: ${err.message || err}`);
            return;
        }

        this.progressPanel = new ProgressPanel(this.schema, this.$progressList, {
            onNavigate: (sid) => this.navigateToSection(sid),
            onEditField: (sid, fid, fieldDef, currentValue) => this._openInline(sid, fid, fieldDef, currentValue),
        });

        // Look for the user's existing in-progress sessions for this schema
        const sessionsResp = await this.api(
            `/api/data-collection/sessions?config_id=${encodeURIComponent(this.configId)}`
        );
        const sessions = (sessionsResp.sessions || []).filter(
            s => s.status === 'in_progress' || s.status === 'review'
        );

        // If the URL has ?resume_session=<id>, target that session specifically
        // (this is what the my-sessions page links use)
        const url = new URL(window.location.href);
        const resumeId = (url.searchParams.get('resume_session') || '').trim();

        if (resumeId) {
            const target = sessions.find(s => s.session_id === resumeId);
            if (target) {
                this.$resumeBtn.style.display = 'flex';
                await this._resumeSession(target);
                return;
            }
            // Fall through if the requested session doesn't belong to us
        }

        if (sessions.length === 0) {
            await this._startNewSession();
            return;
        }

        if (sessions.length === 1) {
            // Single in-progress — auto-resume (preserves the prior UX)
            this.$resumeBtn.style.display = 'flex';
            await this._resumeSession(sessions[0]);
            return;
        }

        // Multiple in-progress for this (user, schema) — let the user pick.
        // This is critical for users who want to keep parallel partial sessions
        // (e.g. drafting two separate event requests at the same time).
        this._showMultiSessionChooser(sessions);
    }

    /**
     * Modal chooser shown when the user has 2+ in-progress sessions for the
     * current schema. Lets them pick one to resume or start a fresh one.
     * Built dynamically so we don't have to add yet another piece of static
     * HTML to the template.
     */
    _showMultiSessionChooser(sessions) {
        // Mutable copy — we splice deleted sessions out as the user discards them
        const list = sessions.slice();

        const overlay = document.createElement('div');
        overlay.className = 'dca-modal';
        overlay.style.display = 'flex';
        overlay.innerHTML = `
            <div class="dca-modal-card" style="max-width:640px;width:92vw;">
                <div class="dca-modal-header">
                    <span><i class="fas fa-list-check"></i> Pick a session to resume</span>
                </div>
                <div class="dca-modal-body" style="max-height:60vh;overflow-y:auto;">
                    <p id="dcaChooserHint" style="color:var(--text-secondary,#a1a1aa);font-size:0.85rem;margin-top:0;"></p>
                    <div id="dcaChooserList" style="display:flex;flex-direction:column;gap:0.5rem;"></div>
                </div>
                <div class="dca-modal-footer">
                    <button class="dca-btn-sm" id="dcaChooserNew" style="width:auto;">
                        <i class="fas fa-plus"></i> Start a new session
                    </button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);

        const $list = overlay.querySelector('#dcaChooserList');
        const $hint = overlay.querySelector('#dcaChooserHint');

        const renderHint = () => {
            $hint.textContent = list.length === 1
                ? 'You have 1 in-progress session for this form. Pick it to resume, or start a new one.'
                : `You have ${list.length} in-progress sessions for this form. Pick one to resume, discard the ones you don't need, or start a new one.`;
        };

        const renderRow = (s) => {
            const updated = (s.updated_at || '').slice(0, 16);
            const sectionLabel = s.current_section_id || '—';
            const filledCount = Object.keys(s.collected_data || {}).reduce((acc, sid) => {
                return acc + Object.keys(s.collected_data[sid] || {}).length;
            }, 0);
            const statusLabel = s.status === 'review'
                ? '<span style="color:#ddd6fe">ready to submit</span>'
                : 'in progress';

            const card = document.createElement('div');
            card.dataset.sessionId = s.session_id;
            card.style.cssText = 'padding:0.75rem;border:1px solid var(--border-color,#27272a);border-radius:6px;background:rgba(255,255,255,0.02);transition:border-color 120ms;display:flex;gap:0.6rem;align-items:flex-start;';
            card.innerHTML = `
                <div data-action="resume" style="flex:1;min-width:0;cursor:pointer;">
                    <div style="font-weight:500;">Session ${this._escape(s.session_id.slice(0, 8))}…</div>
                    <div style="font-size:0.75rem;color:var(--text-tertiary,#71717a);margin-top:0.2rem;">
                        Last updated ${this._escape(updated)} · ${filledCount} field(s) filled · current section: ${this._escape(sectionLabel)}
                    </div>
                    <div style="font-size:0.7rem;color:var(--text-secondary,#a1a1aa);margin-top:0.25rem;">
                        ${statusLabel}
                    </div>
                </div>
                <button data-action="discard"
                        title="Discard this session"
                        style="background:transparent;border:1px solid var(--border-color,#27272a);color:var(--text-tertiary,#71717a);padding:0.4rem 0.55rem;border-radius:6px;cursor:pointer;flex-shrink:0;align-self:center;">
                    <i class="fas fa-trash"></i>
                </button>
            `;

            // Hover affordance for the resume area only (so the discard button
            // doesn't get the "pick to resume" border highlight)
            const resumePane = card.querySelector('[data-action="resume"]');
            resumePane.addEventListener('mouseenter', () => { card.style.borderColor = 'var(--cyber-cyan,#06b6d4)'; });
            resumePane.addEventListener('mouseleave', () => { card.style.borderColor = 'var(--border-color,#27272a)'; });

            // Click resume area → resume that session
            resumePane.addEventListener('click', async () => {
                overlay.remove();
                this.$resumeBtn.style.display = 'flex';
                await this._resumeSession(s);
            });

            // Click discard button → delete the session and update the modal
            const discardBtn = card.querySelector('[data-action="discard"]');
            discardBtn.addEventListener('mouseenter', () => {
                discardBtn.style.color = '#f87171';
                discardBtn.style.borderColor = '#f87171';
            });
            discardBtn.addEventListener('mouseleave', () => {
                discardBtn.style.color = 'var(--text-tertiary,#71717a)';
                discardBtn.style.borderColor = 'var(--border-color,#27272a)';
            });
            discardBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                if (!confirm('Discard this session? Your progress will be lost.')) return;
                discardBtn.disabled = true;
                try {
                    const resp = await fetch(`/api/data-collection/session/${s.session_id}`, {
                        method: 'DELETE',
                        credentials: 'same-origin',
                        headers: { 'Accept': 'application/json' },
                    });
                    if (!resp.ok) throw new Error('HTTP ' + resp.status);
                    // Animate out, splice from list
                    card.style.transition = 'opacity 150ms ease';
                    card.style.opacity = '0.3';
                    setTimeout(() => {
                        card.remove();
                        const idx = list.findIndex(x => x.session_id === s.session_id);
                        if (idx >= 0) list.splice(idx, 1);
                        if (list.length === 0) {
                            // No sessions left — close the modal and start fresh
                            overlay.remove();
                            this._startNewSession();
                        } else {
                            renderHint();
                        }
                    }, 150);
                } catch (e) {
                    discardBtn.disabled = false;
                    alert('Could not discard the session: ' + (e.message || e));
                }
            });

            return card;
        };

        list.forEach(s => $list.appendChild(renderRow(s)));
        renderHint();

        overlay.querySelector('#dcaChooserNew').addEventListener('click', async () => {
            overlay.remove();
            await this._startNewSession();
        });
    }

    // -------------------------------------------------------------------
    // Session lifecycle
    // -------------------------------------------------------------------
    async _startNewSession() {
        this.setStatus('Starting new session…');
        try {
            // Forward the prefill JWT (if the page was loaded with one) to the
            // create-session call so the backend can apply prefill / callback /
            // branding overrides. Empty string when no token in the URL —
            // backend simply ignores it.
            const body = { config_id: this.configId };
            const prefillToken = (window.DCA_CONFIG && window.DCA_CONFIG.prefill_token) || '';
            if (prefillToken) body.prefill_token = prefillToken;
            const resp = await this.api('/api/data-collection/sessions', 'POST', body);
            const session = resp.session;
            this._adoptSession(session);
            this._clearMessages();
            // The server runs the agent's bootstrap_greeting on session create
            // and stores the assistant opening in chat_history. Render whatever
            // it produced — no synthetic user kickoff message needed.
            (session.chat_history || []).forEach(msg => {
                if (msg.role === 'assistant') this._appendMsg('assistant', msg.content);
            });
            // Initialize the progress panel
            this.lastMetadata = {
                phase: 'collecting',
                current_section: session.current_section_id,
                section_status: session.section_status || {},
                collected_data: session.collected_data || {},
                validation_errors: {},
                rich_blocks: [],
                actions: [],
            };
            this._refreshProgress(this.lastMetadata);
            this.setStatus(this._statusText(session.status));
        } catch (err) {
            this.setStatus('Could not start session');
            this._appendMsg('assistant', `Error: ${err.message || err}`);
        }
    }

    async _resumeSession(session) {
        this.setStatus('Resuming previous session…');
        this._adoptSession(session);
        this._clearMessages();

        // Replay chat history
        (session.chat_history || []).forEach(msg => {
            if (msg.role === 'user' || msg.role === 'assistant') {
                this._appendMsg(msg.role, msg.content);
            } else if (msg.role === 'system') {
                this._appendMsg('system', msg.content);
            }
        });

        // Refresh progress panel from the resumed session
        this.lastMetadata = {
            phase: session.status,
            current_section: session.current_section_id,
            section_status: session.section_status || {},
            collected_data: session.collected_data || {},
            validation_errors: {},
            rich_blocks: [],
            actions: [],
        };
        this._refreshProgress(this.lastMetadata);
        this.setStatus(this._statusText(session.status));

        // Decide whether to auto-enable voice mode for this session.
        //
        // Resolution order (most specific wins):
        //   1. localStorage user preference for THIS device
        //      ('dca-voice-mode' = 'on' | 'off') — set when the user
        //      explicitly toggles. Honored over server defaults so an
        //      admin's default_on=true doesn't override a user who chose
        //      manual mode.
        //   2. Resumed session already had voice_mode=true.
        //   3. Server-resolved settings.default_on (admin / schema /
        //      JWT-claim hierarchy).
        const userPref = (() => {
            try { return localStorage.getItem('dca-voice-mode'); } catch (_) { return null; }
        })();

        const tryActivate = () => {
            if (!this.$voiceToggle) return;
            // Defer to allow the controller's lazy init + the page to settle
            setTimeout(() => this._toggleVoiceMode(), 0);
        };

        if (userPref === 'on' || !!session.voice_mode) {
            tryActivate();
        } else if (userPref === 'off') {
            // Explicit user opt-out — don't auto-enable even if admin default is on.
        } else {
            // No user preference set yet. Honor server default.
            this._fetchVoiceSettings().then(s => {
                if (s && s.default_on) tryActivate();
            }).catch(() => {});
        }
    }

    async startNewSession(force = false) {
        if (!force && !confirm('Start over? This will abandon the current session.')) return;
        if (this.sessionId) {
            try {
                await this.api(`/api/data-collection/session/${this.sessionId}`, 'DELETE');
            } catch (err) { /* non-fatal */ }
        }
        this.sessionId = null;
        await this._startNewSession();
    }

    async resumeMostRecent() {
        const sessionsResp = await this.api(
            `/api/data-collection/sessions?config_id=${encodeURIComponent(this.configId)}`
        );
        const resumable = (sessionsResp.sessions || []).find(s => s.status !== 'submitted');
        if (resumable) await this._resumeSession(resumable);
    }

    _adoptSession(session) {
        this.sessionId = session.session_id;
        this.setStatus(this._statusText(session.status));
    }

    // -------------------------------------------------------------------
    // Conversation
    // -------------------------------------------------------------------
    async sendMessage(textOverride = null, isUserVisible = true) {
        const text = textOverride !== null ? textOverride : (this.$input.value || '').trim();
        if (!text || !this.sessionId) return;

        if (isUserVisible) {
            this._appendMsg('user', text);
            this.$input.value = '';
        }
        const $typing = this._appendTyping();
        this._setComposerEnabled(false);

        try {
            const resp = await this.api('/api/data-collection/message', 'POST', {
                session_id: this.sessionId,
                message: text,
            });
            $typing.remove();

            const responseText = resp.response || '';
            const metadata = resp.metadata || {};

            this._appendMsg('assistant', responseText, metadata.rich_blocks || []);
            this.lastMetadata = metadata;
            this._refreshProgress(metadata);
            this._handleActions(metadata.actions || []);
            // Keep the voice controller's phase tracker in sync even when
            // the turn came in through the text path — its auto-listen
            // gate reads this on the next mic-cycle.
            if (this.voice && typeof this.voice.setPhase === 'function') {
                this.voice.setPhase(metadata.phase || metadata.status);
            }
        } catch (err) {
            $typing.remove();
            this._appendMsg('assistant', `Sorry — I hit an error: ${err.message || err}`);
        } finally {
            this._setComposerEnabled(true);
            this.$input.focus();
        }
    }

    _handleActions(actions) {
        for (const action of (actions || [])) {
            if (action.type === 'ready_to_submit') {
                this.$submitBanner.style.display = 'flex';
            } else if (action.type === 'pause_listening') {
                // The user verbally signaled stop / nevermind / pause —
                // the agent called pause_listening, and now we honor it
                // on the frontend by suppressing the next auto-listen.
                if (this.voice && typeof this.voice.pauseAutoListenOnce === 'function') {
                    this.voice.pauseAutoListenOnce(action.reason || '');
                }
            }
            // navigate_to_section is handled implicitly via the next render
        }
    }

    // -------------------------------------------------------------------
    // Section navigation (back/edit from progress panel)
    // -------------------------------------------------------------------
    async navigateToSection(sectionId) {
        if (!this.sessionId) return;
        try {
            await this.api(
                `/api/data-collection/session/${this.sessionId}/navigate`,
                'POST',
                { section_id: sectionId },
            );
            // Tell the agent so it can respond conversationally
            await this.sendMessage(
                `I'd like to revisit the section: ${sectionId}.`,
                /* isUserVisible */ false,
            );
        } catch (err) {
            this._appendMsg('assistant', `Could not navigate: ${err.message || err}`);
        }
    }

    // -------------------------------------------------------------------
    // Inline edit flow
    // -------------------------------------------------------------------
    _findFieldDef(sectionId, fieldId) {
        if (!this.schema || !this.schema.sections) return null;
        const section = this.schema.sections.find(s => s.id === sectionId);
        if (!section || !section.fields) return null;
        return section.fields.find(f => f.id === fieldId) || null;
    }

    _openInline(sectionId, fieldId, fieldDef, currentValueParam) {
        this._inlineEditCtx = { sectionId, fieldId, fieldDef };
        const sectionData = (this.lastMetadata && this.lastMetadata.collected_data && this.lastMetadata.collected_data[sectionId]) || {};
        const currentValue = currentValueParam !== undefined ? currentValueParam : sectionData[fieldId];

        this.$inlineTitle.textContent = `Edit: ${fieldDef.label || fieldId}`;
        this.$inlineBody.innerHTML = this._renderInlineInput(fieldDef, currentValue);
        this.$inlineModal.style.display = 'flex';
        const focusEl = this.$inlineBody.querySelector('input, select, textarea');
        if (focusEl) focusEl.focus();
    }

    _renderInlineInput(fieldDef, currentValue) {
        const val = currentValue === undefined || currentValue === null ? '' : currentValue;
        const t = fieldDef.type;

        if (t === 'textarea') {
            return `
                <label>${this._escape(fieldDef.label || fieldDef.id)}</label>
                <textarea id="dcaInlineInput" rows="4">${this._escape(val)}</textarea>
                <div class="dca-modal-error" id="dcaInlineError"></div>`;
        }
        if (t === 'boolean') {
            return `
                <label>${this._escape(fieldDef.label || fieldDef.id)}</label>
                <select id="dcaInlineInput">
                    <option value="">(unset)</option>
                    <option value="true" ${val === true ? 'selected' : ''}>Yes</option>
                    <option value="false" ${val === false ? 'selected' : ''}>No</option>
                </select>
                <div class="dca-modal-error" id="dcaInlineError"></div>`;
        }
        if (t === 'select' || t === 'lookup') {
            const ref = fieldDef.options_ref || fieldDef.lookup_ref;
            const inline = (fieldDef.options || []);
            const lookup = (this.schema.lookup_data && this.schema.lookup_data[ref]) || null;
            const items = inline.length ? inline : ((lookup && lookup.values) || []);
            const opts = items.map(it => {
                const id = (typeof it === 'object') ? it.id : it;
                const label = (typeof it === 'object') ? (it.label || it.name || id) : it;
                const sel = String(id) === String(val) ? 'selected' : '';
                return `<option value="${this._escape(id)}" ${sel}>${this._escape(label)}</option>`;
            }).join('');
            return `
                <label>${this._escape(fieldDef.label || fieldDef.id)}</label>
                <select id="dcaInlineInput">
                    <option value="">(unset)</option>
                    ${opts}
                </select>
                <div class="dca-modal-error" id="dcaInlineError"></div>`;
        }
        if (t === 'multi_select') {
            const inline = (fieldDef.options || []);
            const ref = fieldDef.options_ref;
            const lookup = ref && this.schema.lookup_data && this.schema.lookup_data[ref];
            const items = inline.length ? inline : ((lookup && lookup.values) || []);
            const valArr = Array.isArray(val) ? val : (val ? [val] : []);
            const opts = items.map(it => {
                const id = (typeof it === 'object') ? it.id : it;
                const label = (typeof it === 'object') ? (it.label || it.name || id) : it;
                const sel = valArr.map(String).includes(String(id)) ? 'selected' : '';
                return `<option value="${this._escape(id)}" ${sel}>${this._escape(label)}</option>`;
            }).join('');
            return `
                <label>${this._escape(fieldDef.label || fieldDef.id)} (hold Ctrl/Cmd to select multiple)</label>
                <select id="dcaInlineInput" multiple size="6">${opts}</select>
                <div class="dca-modal-error" id="dcaInlineError"></div>`;
        }
        // text, number, date, email, phone — single input
        const inputType = ({ number: 'number', date: 'date', email: 'email', phone: 'tel' }[t]) || 'text';
        return `
            <label>${this._escape(fieldDef.label || fieldDef.id)}</label>
            <input id="dcaInlineInput" type="${inputType}" value="${this._escape(val)}">
            <div class="dca-modal-error" id="dcaInlineError"></div>`;
    }

    async _saveInline() {
        if (!this._inlineEditCtx) return;
        const { sectionId, fieldId, fieldDef } = this._inlineEditCtx;
        const inputEl = this.$inlineBody.querySelector('#dcaInlineInput');
        const errEl = this.$inlineBody.querySelector('#dcaInlineError');
        let value;
        if (fieldDef.type === 'multi_select') {
            value = Array.from(inputEl.selectedOptions || []).map(o => o.value);
        } else {
            value = inputEl.value;
        }

        try {
            const resp = await this.api(
                `/api/data-collection/session/${this.sessionId}/update-field`,
                'POST',
                { section_id: sectionId, field_id: fieldId, value },
            );
            // Refresh from the returned session
            const session = resp.session;
            this.lastMetadata = Object.assign({}, this.lastMetadata, {
                collected_data: session.collected_data,
                section_status: session.section_status,
            });
            this._refreshProgress(this.lastMetadata);

            // Update any already-rendered recap_panel rows in the chat
            // for this field so the recap reflects the new value
            // (otherwise the user clicked "edit" ON the recap and the
            // recap kept showing the old value — the most jarring
            // possible UX). Match by data-section-id + data-field-id
            // attributes, which we now stamp on every recap row.
            const updated = (resp && resp.updated_field) || {};
            const newDisplay = (updated.display_value !== undefined)
                ? updated.display_value
                : String(value);
            const rows = document.querySelectorAll(
                `.dca-rp-row[data-section-id="${sectionId}"][data-field-id="${fieldId}"]`
            );
            rows.forEach(row => {
                const valEl = row.querySelector('.dca-rp-value');
                if (valEl) valEl.textContent = newDisplay;
            });

            this._appendMsg(
                'system',
                `Updated "${fieldDef.label || fieldId}" via the progress panel.`,
            );
            this._closeInline();
        } catch (err) {
            errEl.textContent = err.message || String(err);
        }
    }

    _closeInline() {
        this._inlineEditCtx = null;
        this.$inlineModal.style.display = 'none';
    }

    // -------------------------------------------------------------------
    // Submission
    // -------------------------------------------------------------------
    async submit() {
        if (!this.sessionId) return;
        // Show the overlay and start ticking through actions
        this._openSubmissionOverlay();

        try {
            const resp = await this.api(
                `/api/data-collection/session/${this.sessionId}/submit`,
                'POST',
            );
            const pipeline = resp.pipeline || {};
            const results = pipeline.results || [];
            this._renderSubmissionResults(results);
            this.$submissionMessage.textContent = resp.message || '';
            this.$submissionDone.style.display = 'inline-flex';
            // If the deep-link caller sent a return_url claim in their JWT,
            // offer a "Back to <caller>" button next to "Close". This lets
            // MER360 / similar callers smoothly return the user to where
            // they came from after a successful submission.
            this._renderReturnUrlButton(resp.return_url);
            if (resp.session) {
                this.setStatus(this._statusText(resp.session.status));
            }
            if (pipeline.all_success) {
                this.$submitBanner.style.display = 'none';
                this._setComposerEnabled(false);
            }
        } catch (err) {
            this.$submissionMessage.textContent = `Submission failed: ${err.message || err}`;
            this.$submissionDone.style.display = 'inline-flex';
        }
    }

    _openSubmissionOverlay() {
        const actions = (this.schema.completion && this.schema.completion.actions) || [];
        this.$submissionActions.innerHTML = actions.map((a, i) => `
            <li class="pending" data-idx="${i}">
                <span class="dca-submission-icon"><i class="fas fa-spinner fa-spin"></i></span>
                <span>${this._escape(a.label || a.type)}</span>
            </li>
        `).join('');
        this.$submissionMessage.textContent = 'Running completion actions…';
        this.$submissionDone.style.display = 'none';
        this.$submissionOverlay.style.display = 'flex';
    }

    /**
     * Append a "Back to caller" button to the submission overlay when the
     * session has a return_url (set by the JWT prefill claim). Idempotent —
     * subsequent calls replace the existing button rather than appending more.
     */
    _renderReturnUrlButton(returnUrl) {
        const existing = document.getElementById('dcaReturnUrlBtn');
        if (existing) existing.remove();
        if (!returnUrl) return;
        const safeUrl = this._sanitizeReturnUrl(returnUrl);
        if (!safeUrl) return;
        const a = document.createElement('a');
        a.id = 'dcaReturnUrlBtn';
        a.href = safeUrl;
        a.className = 'dca-btn-primary';
        a.style.marginRight = '0.5rem';
        a.style.textDecoration = 'none';
        a.innerHTML = '<i class="fas fa-arrow-left"></i> Back to caller';
        // Insert before the existing "Close" button
        this.$submissionDone.parentNode.insertBefore(a, this.$submissionDone);
    }

    _sanitizeReturnUrl(url) {
        if (!url) return null;
        const s = String(url).trim();
        const lower = s.toLowerCase();
        // Disallow javascript: / data: schemes
        if (lower.startsWith('javascript:') || lower.startsWith('data:')) return null;
        return s;
    }

    _renderSubmissionResults(results) {
        const lis = this.$submissionActions.querySelectorAll('li');
        results.forEach((r, idx) => {
            const $li = lis[idx];
            if (!$li) return;
            $li.classList.remove('pending');
            $li.classList.add(r.success ? 'success' : 'error');
            const icon = r.success ? 'fa-check-circle' : 'fa-times-circle';
            const detail = r.message ? ` — <span style="color:var(--text-tertiary,#71717a);font-size:0.8rem;">${this._escape(r.message)}</span>` : '';
            $li.innerHTML = `
                <span class="dca-submission-icon"><i class="fas ${icon}"></i></span>
                <span>${this._escape(r.label || r.action_type)}${detail}</span>
            `;
        });
    }

    _closeSubmissionOverlay() {
        this.$submissionOverlay.style.display = 'none';
    }

    // -------------------------------------------------------------------
    // Rendering helpers
    // -------------------------------------------------------------------
    _appendMsg(role, content, richBlocks) {
        const row = document.createElement('div');
        row.className = `dca-msg-row ${role}`;
        const bubble = document.createElement('div');
        bubble.className = 'dca-msg-bubble';
        bubble.innerHTML = this._textToHtml(content);
        row.appendChild(bubble);

        if (richBlocks && richBlocks.length && this.renderer) {
            console.log('[dca] rendering %d rich block(s):', richBlocks.length,
                        richBlocks.map(b => (b && b.type) || '?'));
            const richEl = document.createElement('div');
            richEl.className = 'dca-rich-content';
            try {
                const html = this.renderer.render({ blocks: richBlocks });
                if (!html || !html.trim()) {
                    console.error('[dca] renderer returned empty HTML for blocks:',
                        JSON.stringify(richBlocks).slice(0, 500));
                    richEl.style.cssText = 'border:1px dashed #f87171;color:#fca5a5;padding:0.5rem;font-size:0.78rem;border-radius:6px;';
                    richEl.textContent =
                        `Renderer returned empty output for ${richBlocks.length} block(s) of types: `
                        + richBlocks.map(b => (b && b.type) || '?').join(', ')
                        + '. Check DevTools console for details.';
                } else {
                    richEl.innerHTML = html;
                }
            } catch (e) {
                console.error('[dca] richContent render threw:', e,
                    'blocks were:', JSON.stringify(richBlocks).slice(0, 500));
                richEl.style.cssText = 'border:1px dashed #f87171;color:#fca5a5;padding:0.5rem;font-size:0.78rem;border-radius:6px;font-family:monospace;white-space:pre-wrap;';
                richEl.textContent = `Render error: ${e.message || e}\n\n`
                    + 'Block payload: '
                    + JSON.stringify(richBlocks, null, 2);
            }
            bubble.appendChild(richEl);
        } else if (richBlocks && richBlocks.length && !this.renderer) {
            console.error('[dca] received', richBlocks.length, 'rich blocks but no renderer is initialized');
        }
        this.$messages.appendChild(row);
        this.$messages.scrollTop = this.$messages.scrollHeight;
    }

    _appendTyping() {
        const row = document.createElement('div');
        row.className = 'dca-msg-row assistant';
        row.innerHTML = `
            <div class="dca-typing">
                <span class="dca-typing-dot"></span>
                <span class="dca-typing-dot"></span>
                <span class="dca-typing-dot"></span>
            </div>`;
        this.$messages.appendChild(row);
        this.$messages.scrollTop = this.$messages.scrollHeight;
        return row;
    }

    _clearMessages() {
        this.$messages.innerHTML = '';
        // Whenever the chat is wiped (Reset, Start Over, Resume, restart_session
        // tool, etc.) any "Ready to submit?" banner from a prior turn is stale
        // and should disappear. The banner is shown again automatically when
        // the agent re-enters review phase via the ready_to_submit action.
        if (this.$submitBanner) this.$submitBanner.style.display = 'none';
    }

    _refreshProgress(metadata) {
        if (!this.progressPanel || !this.schema) return;
        const state = {
            section_status: metadata.section_status || {},
            collected_data: metadata.collected_data || {},
            current_section: metadata.current_section,
            validation_errors: metadata.validation_errors || {},
            // Conditional visibility: server-computed map of
            // {section_id: [field_id, ...]} for fields whose
            // show_when condition currently evaluates true. If
            // missing, the panel falls back to showing all fields.
            visible_fields: metadata.visible_fields || null,
        };
        this.progressPanel.render(state);

        // Progress bar
        const pct = ProgressPanel.computePercent(this.schema, state.collected_data);
        this.$progressFill.style.width = `${pct}%`;
        this.$progressLabel.textContent = `${pct}%`;

        // Section counter
        const counts = ProgressPanel.computeSectionCounts(this.schema, state.section_status);
        this.$sectionCounter.textContent = `${counts.complete} / ${counts.total}`;

        // Submit banner visibility
        const phase = metadata.phase;
        if (phase === 'review' || metadata.status === 'review') {
            this.$submitBanner.style.display = 'flex';
        }
    }

    setStatus(text) { this.$status.textContent = text; }

    _statusText(status) {
        return ({
            'in_progress': 'In progress',
            'review': 'Ready for review',
            'submitted': 'Submitted',
            'submission_failed': 'Submission failed',
            'draft': 'Draft',
        }[status]) || status || '';
    }

    _setComposerEnabled(enabled) {
        this.$input.disabled = !enabled;
        this.$sendBtn.disabled = !enabled;
    }

    /**
     * Mobile drawer behavior — wire the hamburger / progress buttons in the
     * mobile header to slide the left sidebar / right progress panel in and
     * out. The buttons are hidden on desktop via CSS, so this is harmless
     * on larger screens.
     */
    _wireDrawers() {
        const $sidebarBtn = document.getElementById('dcaSidebarToggle');
        const $progressBtn = document.getElementById('dcaProgressToggle');
        const $backdrop = document.getElementById('dcaDrawerBackdrop');

        const closeAll = () => {
            document.body.classList.remove('dca-show-sidebar', 'dca-show-progress');
        };
        const openSidebar = () => {
            document.body.classList.remove('dca-show-progress');
            document.body.classList.add('dca-show-sidebar');
        };
        const openProgress = () => {
            document.body.classList.remove('dca-show-sidebar');
            document.body.classList.add('dca-show-progress');
        };

        if ($sidebarBtn) {
            $sidebarBtn.addEventListener('click', () => {
                document.body.classList.contains('dca-show-sidebar') ? closeAll() : openSidebar();
            });
        }
        if ($progressBtn) {
            $progressBtn.addEventListener('click', () => {
                document.body.classList.contains('dca-show-progress') ? closeAll() : openProgress();
            });
        }
        if ($backdrop) {
            $backdrop.addEventListener('click', closeAll);
        }

        // Close drawers when the user resizes back to desktop, so we don't
        // leave classes stuck on the body.
        let lastWide = window.innerWidth >= 1024;
        window.addEventListener('resize', () => {
            const wide = window.innerWidth >= 1024;
            if (wide && !lastWide) closeAll();
            lastWide = wide;
        });

        // Auto-close progress drawer when the user clicks a section to navigate
        // (so the action they took is visible behind the drawer rather than
        // hidden by it).
        if (this.$progressList) {
            this.$progressList.addEventListener('click', (e) => {
                if (window.innerWidth >= 768) return;  // desktop/tablet — leave alone
                const action = e.target.closest('[data-action="navigate"]');
                if (action) {
                    setTimeout(closeAll, 250);  // small delay so user sees the click landed
                }
            });
        }
    }

    /**
     * Activate / deactivate voice mode. Creates the controller lazily so
     * users who never use voice don't pay the cost of loading the
     * SpeechRecognitionManager.
     *
     * Pre-flight: refuses to enter voice mode when the page isn't loaded
     * over a secure context (HTTPS or localhost). Browsers reject
     * `getUserMedia()` on plain HTTP origins, so without this check the
     * user would tap the mic and get a confusing silent failure.
     * `window.isSecureContext` is the browser's authoritative answer:
     * true on HTTPS and on the loopback hostnames (localhost, 127.0.0.1, ::1),
     * false everywhere else.
     */
    async _toggleVoiceMode() {
        // If we're already in voice mode, deactivating doesn't need a mic
        // — let it through regardless of secure context.
        if (this.voice && this.voice.active) {
            await this.voice.deactivate();
            return;
        }

        // Activation path: secure-context check first.
        if (!window.isSecureContext) {
            this._showInsecureContextNotice();
            return;
        }

        if (!this.voice) {
            // Voice-mode-streaming.js exposes window.StreamingVoiceController.
            // (Phase 2.9 will add the orchestrator that prefers Realtime over
            // streaming-hybrid when available.)
            const Ctor = window.StreamingVoiceController;
            if (!Ctor) {
                console.warn('[dca] voice controller not loaded');
                return;
            }
            this.voice = new Ctor(this, {
                $mic: this.$voiceMic,
                $state: this.$voiceState,
                $voiceComposer: this.$voiceComposer,
                $textComposer: this.$textComposer,
                $voiceToggle: this.$voiceToggle,
                $muteToggle: this.$muteToggle,
                $cancel: this.$voiceCancel,
            });
            // Apply already-fetched settings if we have them (default-on path
            // fetches first, manual-toggle path may not have).
            if (this._voiceSettings) {
                this.voice.setSettings(this._voiceSettings);
            } else {
                // Fire-and-forget settings fetch — the controller stays
                // safe with defaults until they arrive.
                this._fetchVoiceSettings().then(s => {
                    if (s && this.voice) this.voice.setSettings(s);
                }).catch(() => {});
            }
        }
        await this.voice.activate();
    }

    /**
     * Fetch and cache the resolved voice settings (default_on, auto_listen,
     * silence_threshold_ms, listen_timeout_ms, auto_listen_only_when_collecting)
     * for this form. Public-read endpoint — no auth required, settings don't
     * expose anything sensitive.
     */
    async _fetchVoiceSettings() {
        if (this._voiceSettings) return this._voiceSettings;
        try {
            const resp = await fetch(
                `/api/data-collection/${encodeURIComponent(this.configId)}/voice-settings`,
                { credentials: 'same-origin' }
            );
            if (!resp.ok) return null;
            const body = await resp.json();
            if (body && body.status === 'success' && body.settings) {
                this._voiceSettings = body.settings;
                return this._voiceSettings;
            }
        } catch (e) {
            console.warn('[dca] _fetchVoiceSettings failed:', e);
        }
        return null;
    }

    /**
     * Show the insecure-context banner with voice-mode-specific wording,
     * inserting it into the page if not already present. Same visual
     * treatment as the schema's `requires_secure_context` banner so the
     * page stays consistent.
     */
    _showInsecureContextNotice() {
        const banner = document.getElementById('dcaInsecureBanner');
        if (banner) {
            banner.style.display = '';
            banner.innerHTML = `
                <i class="fas fa-shield-halved"></i>
                <div>
                    <strong>Voice mode requires a secure connection (HTTPS).</strong>
                    Microphones don't work over plain HTTP — this is a browser-level
                    restriction we can't override. Ask your administrator to enable
                    HTTPS, or open this app from
                    <a href="http://localhost:5099/data-collection/" style="color:inherit;text-decoration:underline;">localhost</a>
                    for testing.
                </div>
            `;
            // Make sure the user notices it
            try { banner.scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch (_) {}
            // Auto-hide after a moment so it doesn't clutter the page forever
            clearTimeout(this._insecureBannerTimer);
            this._insecureBannerTimer = setTimeout(() => {
                banner.style.display = 'none';
            }, 8000);
        }
        console.warn('[dca] voice mode requires HTTPS — current origin is not a secure context');
    }

    toggleTheme() {
        document.body.classList.toggle('light-mode');
        const icon = this.$themeBtn.querySelector('i');
        if (icon) icon.className = document.body.classList.contains('light-mode') ? 'fas fa-sun' : 'fas fa-moon';
        try {
            localStorage.setItem('dca-theme', document.body.classList.contains('light-mode') ? 'light' : 'dark');
        } catch (_) { /* noop */ }
    }

    // -------------------------------------------------------------------
    // API + utility
    // -------------------------------------------------------------------
    async api(url, method = 'GET', body = null) {
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
        try {
            payload = await resp.json();
        } catch (_) {
            payload = { status: 'error', error: `HTTP ${resp.status}` };
        }
        if (!resp.ok && payload && payload.status === 'error') {
            const err = new Error(payload.error || `HTTP ${resp.status}`);
            err.payload = payload;
            throw err;
        }
        return payload;
    }

    _textToHtml(text) {
        if (!text) return '';
        // Minimal markdown-ish: escape, then convert newlines and **bold**
        const esc = this._escape(text);
        return esc
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/\n/g, '<br>');
    }

    _escape(s) {
        if (s === null || s === undefined) return '';
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }
}

window.DataCollectionApp = DataCollectionApp;

// Apply persisted theme preference on load
(function() {
    try {
        const pref = localStorage.getItem('dca-theme');
        if (pref === 'light') document.body.classList.add('light-mode');
    } catch (_) { /* noop */ }
})();
