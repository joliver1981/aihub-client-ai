/**
 * schema-editor.js
 *
 * The form-based editor for the right pane of the Schema Builder Wizard.
 *
 * Owns the rendering and interaction of:
 *   - Schema metadata fields (id, name, description, version, agent_guidelines)
 *   - Sections list (add / edit / delete / reorder; expand to manage fields)
 *   - Fields editor modal (type-specific options, validation rules)
 *   - Lookup data list + modal
 *   - Completion actions list + per-type editor modal
 *   - Validation results display
 *   - Raw JSON view
 *
 * Stays in sync with the schema state held by SchemaBuilderApp via:
 *   - .render(schema, validation)   // re-render everything from authoritative state
 *   - opts.onChange(newSchema)      // emit when the user edits anything
 *
 * No network calls — that's SchemaBuilderApp's job.
 */

const FIELD_TYPES = [
    'text', 'textarea', 'number', 'date', 'boolean',
    'select', 'multi_select', 'lookup',
    'email', 'phone', 'file',
];

const VALIDATION_RULES = [
    { value: '', label: '(no special rule)' },
    { value: 'future_date', label: 'future_date — date must be in the future' },
    { value: 'in_list', label: 'in_list — value must be in a defined list' },
    { value: 'pattern', label: 'pattern — value matches a regex' },
    { value: 'email', label: 'email — valid email format' },
    { value: 'phone', label: 'phone — valid phone number' },
];

const ACTION_TYPE_DESCRIPTIONS = {
    email: 'Send an email summary to specified recipients.',
    sms:   'Send a text message via the platform notification service.',
    workflow: 'Trigger a platform workflow with the collected data.',
    api: 'POST/PUT/PATCH the collected data to an external REST API.',
    webhook: 'Fire a webhook payload to a hosted automation (Zapier, Make, etc.).',
    agent: 'Hand off to another platform AI agent for downstream processing.',
};


class SchemaEditor {
    constructor(rootEl, opts = {}) {
        this.root = rootEl;
        this.onChange = opts.onChange || (() => {});

        // DOM refs
        this.$id = document.getElementById('dcaSchemaId');
        this.$name = document.getElementById('dcaSchemaName');
        this.$desc = document.getElementById('dcaSchemaDescription');
        this.$version = document.getElementById('dcaSchemaVersion');
        this.$guidelines = document.getElementById('dcaSchemaGuidelines');

        // Branding inputs (all optional, schema-level)
        this.$brandDisplay = document.getElementById('dcaBrandingDisplayName');
        this.$brandLogo = document.getElementById('dcaBrandingLogoUrl');
        this.$brandPrimary = document.getElementById('dcaBrandingPrimaryColor');
        this.$brandAccent = document.getElementById('dcaBrandingAccentColor');
        this.$brandFooter = document.getElementById('dcaBrandingFooterText');
        this.$brandFavicon = document.getElementById('dcaBrandingFaviconUrl');
        // Schema-level deployment hint (advisory, not enforced)
        this.$requiresSecureContext = document.getElementById('dcaRequiresSecureContext');

        this.$sectionsList = document.getElementById('dcaSectionsList');
        this.$sectionsCount = document.getElementById('dcaSectionsCount');
        this.$addSectionBtn = document.getElementById('dcaAddSectionBtn');

        this.$lookupsList = document.getElementById('dcaLookupsList');
        this.$lookupsCount = document.getElementById('dcaLookupsCount');
        this.$addLookupBtn = document.getElementById('dcaAddLookupBtn');

        this.$customToolsList = document.getElementById('dcaCustomToolsList');
        this.$customToolsCount = document.getElementById('dcaCustomToolsCount');

        this.$confirmMessage = document.getElementById('dcaConfirmMessage');
        this.$actionsList = document.getElementById('dcaActionsList');
        this.$actionsCount = document.getElementById('dcaActionsCount');
        this.$newActionType = document.getElementById('dcaNewActionType');
        this.$addActionBtn = document.getElementById('dcaAddActionBtn');

        this.$validationCard = document.getElementById('dcaValidationCard');
        this.$validationCount = document.getElementById('dcaValidationCount');
        this.$validationBody = document.getElementById('dcaValidationBody');

        this.$rawJson = document.getElementById('dcaRawJson');

        // Modals
        this.$fieldModal = document.getElementById('dcaFieldModal');
        this.$fieldModalTitle = document.getElementById('dcaFieldModalTitle');
        this.$fieldModalBody = document.getElementById('dcaFieldModalBody');
        this.$fieldModalSave = document.getElementById('dcaFieldModalSave');
        this.$fieldModalCancel = document.getElementById('dcaFieldModalCancel');
        this.$fieldModalClose = document.getElementById('dcaFieldModalClose');

        this.$actionModal = document.getElementById('dcaActionModal');
        this.$actionModalTitle = document.getElementById('dcaActionModalTitle');
        this.$actionModalBody = document.getElementById('dcaActionModalBody');
        this.$actionModalSave = document.getElementById('dcaActionModalSave');
        this.$actionModalCancel = document.getElementById('dcaActionModalCancel');
        this.$actionModalClose = document.getElementById('dcaActionModalClose');

        this.$lookupModal = document.getElementById('dcaLookupModal');
        this.$lookupModalTitle = document.getElementById('dcaLookupModalTitle');
        this.$lookupModalBody = document.getElementById('dcaLookupModalBody');
        this.$lookupModalSave = document.getElementById('dcaLookupModalSave');
        this.$lookupModalCancel = document.getElementById('dcaLookupModalCancel');
        this.$lookupModalClose = document.getElementById('dcaLookupModalClose');

        this.schema = null;
        this._fieldEditCtx = null;     // {sectionId, fieldId|null, draft}
        this._actionEditCtx = null;    // {index|null, draft}
        this._lookupEditCtx = null;    // {ref|null, draft}

        // Cached pickers
        this.workflowsList = [];
        this.agentsList = [];
        this.connectionsList = [];   // [{id, name, server, database, type}]
        this.customToolsList = [];   // [{name, function_name, description, parameters}]

        this._wireMetadataChange();
        this._wireToolbar();
        this._wireModals();
    }

    setPickers({ workflows, agents, connections, customTools }) {
        this.workflowsList = workflows || [];
        this.agentsList = agents || [];
        this.connectionsList = connections || [];
        this.customToolsList = customTools || [];
    }

    // ------------------------------------------------------------------
    render(schema, validation) {
        this.schema = schema || {};
        this._renderMetadata();
        this._renderSections();
        this._renderLookups();
        this._renderCustomTools();
        this._renderActions();
        this._renderValidation(validation);
        this._renderRawJson();
    }

    _renderMetadata() {
        this.$id.value = this.schema.id || '';
        this.$name.value = this.schema.name || '';
        this.$desc.value = this.schema.description || '';
        this.$version.value = this.schema.version || '1.0';
        this.$guidelines.value = this.schema.agent_guidelines || '';
        // Branding (optional)
        const b = this.schema.branding || {};
        if (this.$brandDisplay) this.$brandDisplay.value = b.display_name || '';
        if (this.$brandLogo) this.$brandLogo.value = b.logo_url || '';
        if (this.$brandPrimary) this.$brandPrimary.value = b.primary_color || '';
        if (this.$brandAccent) this.$brandAccent.value = b.accent_color || '';
        if (this.$brandFooter) this.$brandFooter.value = b.footer_text || '';
        if (this.$brandFavicon) this.$brandFavicon.value = b.favicon_url || '';
        if (this.$requiresSecureContext) {
            this.$requiresSecureContext.checked = !!this.schema.requires_secure_context;
        }
    }

    _renderSections() {
        const sections = (this.schema.sections || []).slice()
            .sort((a, b) => (a.order || 999) - (b.order || 999));
        this.$sectionsCount.textContent = sections.length;
        this.$sectionsList.innerHTML = sections.map((s, idx) => `
            <div class="dca-section-card" data-sid="${esc(s.id)}">
                <div class="dca-section-card-head">
                    <span class="dca-section-card-id">${esc(s.id)}</span>
                    <span class="dca-section-card-title">${esc(s.title || '')}</span>
                    <div class="dca-section-card-actions">
                        <button class="dca-icon-btn" data-action="up" title="Move up">
                            <i class="fas fa-arrow-up"></i></button>
                        <button class="dca-icon-btn" data-action="down" title="Move down">
                            <i class="fas fa-arrow-down"></i></button>
                        <button class="dca-icon-btn" data-action="edit-section" title="Edit section">
                            <i class="fas fa-edit"></i></button>
                        <button class="dca-icon-btn danger" data-action="delete-section" title="Delete">
                            <i class="fas fa-trash"></i></button>
                    </div>
                </div>
                <div class="dca-fields-list" data-sid="${esc(s.id)}">
                    ${(s.fields || []).map(f => `
                        <div class="dca-field-card" data-fid="${esc(f.id)}">
                            <span class="dca-field-card-id">${esc(f.id)}</span>
                            <span class="dca-field-card-label">${esc(f.label || '')}</span>
                            <span class="dca-field-card-type">${esc(f.type || '')}</span>
                            ${f.required ? '<span class="dca-field-card-required" title="Required">*</span>' : ''}
                            <button class="dca-icon-btn" data-action="edit-field"><i class="fas fa-edit"></i></button>
                            <button class="dca-icon-btn danger" data-action="delete-field"><i class="fas fa-trash"></i></button>
                        </div>
                    `).join('')}
                    <button class="dca-btn-sm" data-action="add-field"
                            style="margin-top:0.4rem;">
                        <i class="fas fa-plus"></i> Add field
                    </button>
                </div>
            </div>
        `).join('');

        this._wireSectionsList();
    }

    _wireSectionsList() {
        this.$sectionsList.querySelectorAll('.dca-section-card').forEach(card => {
            const sid = card.dataset.sid;
            card.querySelectorAll('[data-action]').forEach(btn => {
                btn.addEventListener('click', e => {
                    e.stopPropagation();
                    const action = btn.dataset.action;
                    if (action === 'up') this._moveSection(sid, -1);
                    else if (action === 'down') this._moveSection(sid, +1);
                    else if (action === 'edit-section') this._editSection(sid);
                    else if (action === 'delete-section') this._deleteSection(sid);
                    else if (action === 'add-field') this._openFieldModal(sid, null);
                    else if (action === 'edit-field') {
                        const fid = btn.closest('.dca-field-card').dataset.fid;
                        this._openFieldModal(sid, fid);
                    }
                    else if (action === 'delete-field') {
                        const fid = btn.closest('.dca-field-card').dataset.fid;
                        this._deleteField(sid, fid);
                    }
                });
            });
        });
    }

    _renderLookups() {
        const lookups = this.schema.lookup_data || {};
        const refs = Object.keys(lookups);
        this.$lookupsCount.textContent = refs.length;
        this.$lookupsList.innerHTML = refs.map(ref => {
            const l = lookups[ref] || {};
            let summary = '';
            if (l.source === 'database') {
                const conn = this.connectionsList.find(c => c.id === l.connection_id);
                const connName = conn ? conn.name : `id=${l.connection_id || '?'}`;
                summary = `database — ${esc(connName)} · ${esc(l.view || '?')}`
                        + (l.select_columns ? ` · ${l.select_columns.length} cols` : '');
            } else if (l.source === 'file') {
                summary = `file — ${esc(l.file || '')}`;
            } else {
                summary = `inline — ${(l.values || []).length} value(s)`;
            }
            return `
                <div class="dca-lookup-card" data-ref="${esc(ref)}">
                    <span class="dca-lookup-card-name">${esc(ref)}</span>
                    <span class="dca-lookup-card-source">${summary}</span>
                    <button class="dca-icon-btn" data-action="edit-lookup">
                        <i class="fas fa-edit"></i></button>
                    <button class="dca-icon-btn danger" data-action="delete-lookup">
                        <i class="fas fa-trash"></i></button>
                </div>
            `;
        }).join('');
        this.$lookupsList.querySelectorAll('[data-action]').forEach(btn => {
            btn.addEventListener('click', e => {
                e.stopPropagation();
                const ref = btn.closest('[data-ref]').dataset.ref;
                if (btn.dataset.action === 'edit-lookup') this._openLookupModal(ref);
                else if (btn.dataset.action === 'delete-lookup') this._deleteLookup(ref);
            });
        });
    }

    _renderCustomTools() {
        if (!this.$customToolsList) return;
        const selected = new Set((this.schema.custom_tools || []).map(String));
        const available = this.customToolsList || [];
        if (this.$customToolsCount) {
            this.$customToolsCount.textContent = selected.size;
        }
        if (!available.length) {
            this.$customToolsList.innerHTML = `
                <div class="dca-form-help" style="color:var(--text-tertiary,#71717a);">
                    No platform custom tools detected (the
                    <code>tools/</code> folder is empty or unreadable).
                    Add a tool there with <code>config.json</code> +
                    <code>code.py</code> to see it here.
                </div>`;
            return;
        }
        this.$customToolsList.innerHTML = available.map(t => {
            const checked = selected.has(t.name) ? 'checked' : '';
            const params = (t.parameters || []).join(', ') || '(no params)';
            return `
                <label class="dca-custom-tool-row" style="display:flex;gap:0.55rem;align-items:flex-start;padding:0.5rem 0.6rem;border:1px solid var(--border-color,#27272a);border-radius:6px;margin-bottom:0.4rem;cursor:pointer;">
                    <input type="checkbox" class="dca-custom-tool-cb"
                           data-tool-name="${esc(t.name)}" ${checked}
                           style="margin-top:0.25rem;">
                    <div style="flex:1;min-width:0;">
                        <div style="font-weight:600;font-size:0.92rem;">
                            ${esc(t.function_name || t.name)}
                            <code style="font-weight:400;font-size:0.75rem;color:var(--text-tertiary,#71717a);margin-left:0.4rem;">${esc(t.name)}</code>
                        </div>
                        <div style="font-size:0.82rem;color:var(--text-secondary,#a1a1aa);line-height:1.4;margin-top:0.2rem;">
                            ${esc(t.description || '(no description)')}
                        </div>
                        <div style="font-size:0.72rem;color:var(--text-tertiary,#71717a);margin-top:0.25rem;font-family:'JetBrains Mono',monospace;">
                            params: ${esc(params)}
                        </div>
                    </div>
                </label>
            `;
        }).join('');
        this.$customToolsList.querySelectorAll('.dca-custom-tool-cb').forEach(cb => {
            cb.addEventListener('change', () => {
                const name = cb.dataset.toolName;
                const list = new Set(this.schema.custom_tools || []);
                if (cb.checked) list.add(name);
                else list.delete(name);
                this.schema.custom_tools = Array.from(list);
                if (this.$customToolsCount) {
                    this.$customToolsCount.textContent = this.schema.custom_tools.length;
                }
                this._emit();
            });
        });
    }

    _renderActions() {
        const completion = this.schema.completion || {};
        this.$confirmMessage.value = completion.confirmation_message || '';
        const actions = completion.actions || [];
        this.$actionsCount.textContent = actions.length;
        this.$actionsList.innerHTML = actions.map((a, idx) => `
            <div class="dca-action-card" data-idx="${idx}">
                <span class="dca-action-card-type">${esc(a.type || '')}</span>
                <span class="dca-action-card-label">${esc(a.label || a.type || 'action')}</span>
                <button class="dca-icon-btn" data-action="up" title="Move up"><i class="fas fa-arrow-up"></i></button>
                <button class="dca-icon-btn" data-action="down" title="Move down"><i class="fas fa-arrow-down"></i></button>
                <button class="dca-icon-btn" data-action="edit-action"><i class="fas fa-edit"></i></button>
                <button class="dca-icon-btn danger" data-action="delete-action"><i class="fas fa-trash"></i></button>
            </div>
        `).join('');
        this.$actionsList.querySelectorAll('[data-action]').forEach(btn => {
            btn.addEventListener('click', e => {
                e.stopPropagation();
                const idx = parseInt(btn.closest('[data-idx]').dataset.idx, 10);
                if (btn.dataset.action === 'up') this._moveAction(idx, -1);
                else if (btn.dataset.action === 'down') this._moveAction(idx, +1);
                else if (btn.dataset.action === 'edit-action') this._openActionModal(idx);
                else if (btn.dataset.action === 'delete-action') this._deleteAction(idx);
            });
        });
    }

    _renderValidation(validation) {
        const v = validation || { errors: [], warnings: [] };
        const total = (v.errors || []).length + (v.warnings || []).length;
        this.$validationCount.textContent = total;
        if (total === 0) {
            this.$validationBody.innerHTML = '<p class="dca-help">No issues. The schema looks well-formed.</p>';
            return;
        }
        const items = [
            ...(v.errors || []).map(e => ({ ...e, kind: 'error' })),
            ...(v.warnings || []).map(w => ({ ...w, kind: 'warning' })),
        ];
        this.$validationBody.innerHTML = `
            <ul class="dca-validation-list">
                ${items.map(it => `
                    <li class="${it.kind}">
                        <span class="path">${esc(it.path)}</span>
                        ${esc(it.message)}
                    </li>
                `).join('')}
            </ul>
        `;
    }

    _renderRawJson() {
        try {
            this.$rawJson.textContent = JSON.stringify(this.schema, null, 2);
        } catch (_) {
            this.$rawJson.textContent = '(error serializing schema)';
        }
    }

    // ------------------------------------------------------------------
    // Metadata change
    // ------------------------------------------------------------------
    _wireMetadataChange() {
        const handler = () => {
            this.schema.id = this.$id.value.trim();
            this.schema.name = this.$name.value.trim();
            this.schema.description = this.$desc.value;
            this.schema.version = this.$version.value;
            this.schema.agent_guidelines = this.$guidelines.value;
            this._emit();
        };
        [this.$id, this.$name, this.$desc, this.$version, this.$guidelines]
            .forEach(el => el.addEventListener('change', handler));

        this.$confirmMessage.addEventListener('change', () => {
            this.schema.completion = this.schema.completion || {};
            this.schema.completion.confirmation_message = this.$confirmMessage.value;
            this._emit();
        });

        // Branding inputs — write to schema.branding, drop the block if every
        // field is empty so we don't bloat the saved file with empty objects.
        const brandHandler = () => {
            const b = {};
            if (this.$brandDisplay && this.$brandDisplay.value.trim()) b.display_name = this.$brandDisplay.value.trim();
            if (this.$brandLogo && this.$brandLogo.value.trim()) b.logo_url = this.$brandLogo.value.trim();
            if (this.$brandPrimary && this.$brandPrimary.value.trim()) b.primary_color = this.$brandPrimary.value.trim();
            if (this.$brandAccent && this.$brandAccent.value.trim()) b.accent_color = this.$brandAccent.value.trim();
            if (this.$brandFooter && this.$brandFooter.value.trim()) b.footer_text = this.$brandFooter.value.trim();
            if (this.$brandFavicon && this.$brandFavicon.value.trim()) b.favicon_url = this.$brandFavicon.value.trim();
            if (Object.keys(b).length === 0) {
                delete this.schema.branding;
            } else {
                this.schema.branding = b;
            }
            this._emit();
        };
        [this.$brandDisplay, this.$brandLogo, this.$brandPrimary, this.$brandAccent,
         this.$brandFooter, this.$brandFavicon]
            .forEach(el => { if (el) el.addEventListener('change', brandHandler); });

        if (this.$requiresSecureContext) {
            this.$requiresSecureContext.addEventListener('change', () => {
                if (this.$requiresSecureContext.checked) {
                    this.schema.requires_secure_context = true;
                } else {
                    delete this.schema.requires_secure_context;
                }
                this._emit();
            });
        }
    }

    _wireToolbar() {
        this.$addSectionBtn.addEventListener('click', () => this._addSection());
        this.$addLookupBtn.addEventListener('click', () => this._openLookupModal(null));
        this.$addActionBtn.addEventListener('click', () => {
            const type = this.$newActionType.value;
            this._openActionModal(null, type);
        });
    }

    _wireModals() {
        // Field modal
        this.$fieldModalCancel.addEventListener('click', () => this._closeModal(this.$fieldModal));
        this.$fieldModalClose.addEventListener('click', () => this._closeModal(this.$fieldModal));
        this.$fieldModalSave.addEventListener('click', () => this._saveFieldModal());

        // Action modal
        this.$actionModalCancel.addEventListener('click', () => this._closeModal(this.$actionModal));
        this.$actionModalClose.addEventListener('click', () => this._closeModal(this.$actionModal));
        this.$actionModalSave.addEventListener('click', () => this._saveActionModal());

        // Lookup modal
        this.$lookupModalCancel.addEventListener('click', () => this._closeModal(this.$lookupModal));
        this.$lookupModalClose.addEventListener('click', () => this._closeModal(this.$lookupModal));
        this.$lookupModalSave.addEventListener('click', () => this._saveLookupModal());
    }

    // ------------------------------------------------------------------
    // Section operations
    // ------------------------------------------------------------------
    _addSection() {
        const sections = this.schema.sections = this.schema.sections || [];
        const id = prompt('Section ID (snake_case):');
        if (!id) return;
        if (sections.find(s => s.id === id)) {
            alert(`Section "${id}" already exists.`);
            return;
        }
        const title = prompt('Section title:') || id;
        sections.push({
            id, title, description: '', order: sections.length + 1, fields: [],
        });
        this._emit();
    }

    _editSection(sid) {
        const section = (this.schema.sections || []).find(s => s.id === sid);
        if (!section) return;
        const title = prompt('Section title:', section.title || '');
        if (title === null) return;
        section.title = title;
        const desc = prompt('Section description:', section.description || '');
        if (desc !== null) section.description = desc;
        this._emit();
    }

    _deleteSection(sid) {
        if (!confirm(`Delete section "${sid}"? This removes its fields too.`)) return;
        this.schema.sections = (this.schema.sections || []).filter(s => s.id !== sid);
        this._emit();
    }

    _moveSection(sid, delta) {
        const sections = this.schema.sections || [];
        sections.sort((a, b) => (a.order || 999) - (b.order || 999));
        const idx = sections.findIndex(s => s.id === sid);
        if (idx === -1) return;
        const target = idx + delta;
        if (target < 0 || target >= sections.length) return;
        // Swap order values
        const tmp = sections[idx].order || (idx + 1);
        sections[idx].order = sections[target].order || (target + 1);
        sections[target].order = tmp;
        this._emit();
    }

    // ------------------------------------------------------------------
    // Field modal
    // ------------------------------------------------------------------
    _openFieldModal(sid, fid) {
        const section = (this.schema.sections || []).find(s => s.id === sid);
        if (!section) return;
        const existing = fid ? (section.fields || []).find(f => f.id === fid) : null;
        const draft = existing
            ? JSON.parse(JSON.stringify(existing))
            : { id: '', label: '', type: 'text', required: false };
        this._fieldEditCtx = { sectionId: sid, fieldId: fid, draft };
        this.$fieldModalTitle.textContent = existing ? `Edit field: ${fid}` : 'Add field';
        this.$fieldModalBody.innerHTML = this._renderFieldForm(draft);
        this._wireFieldFormEvents(draft);
        this.$fieldModal.style.display = 'flex';
    }

    _renderFieldForm(draft) {
        const lookupRefs = Object.keys(this.schema.lookup_data || {});
        return `
            <label>Field ID (snake_case)
                <input type="text" id="fldId" value="${esc(draft.id || '')}">
            </label>
            <label>Label
                <input type="text" id="fldLabel" value="${esc(draft.label || '')}">
            </label>
            <label>Type
                <select id="fldType">
                    ${FIELD_TYPES.map(t => `<option value="${t}" ${draft.type === t ? 'selected' : ''}>${t}</option>`).join('')}
                </select>
            </label>
            <label>
                <input type="checkbox" id="fldRequired" ${draft.required ? 'checked' : ''}>
                Required
            </label>
            <label>Prompt hint (what should the AI ask?)
                <input type="text" id="fldHint" value="${esc(draft.prompt_hint || '')}">
            </label>

            <div id="fldTypeSpecific"></div>

            <label>Validation
                <select id="fldValidationRule">
                    ${VALIDATION_RULES.map(r => `
                        <option value="${r.value}" ${(draft.validation && draft.validation.rule === r.value) ? 'selected' : ''}>
                            ${esc(r.label)}
                        </option>
                    `).join('')}
                </select>
            </label>
            <div id="fldValidationParams"></div>

            <p class="dca-form-help">
                For numbers, set min/max under "validation" with the JSON editor below.
                For text, set min_length/max_length the same way.
            </p>
            <label>Validation JSON (raw — overrides the rule above if both set)
                <textarea id="fldValidationJson" rows="3"
                    placeholder='e.g. {"rule":"future_date","min_days_ahead":7}'>${esc(JSON.stringify(draft.validation || {}, null, 2))}</textarea>
            </label>

            <label>Conditional show_when JSON (optional)
                <textarea id="fldConditional" rows="3"
                    placeholder='e.g. {"show_when":{"field":"some_flag","operator":"==","value":true}}'>${esc(JSON.stringify(draft.conditional || {}, null, 2))}</textarea>
            </label>

            <div class="dca-modal-error" id="fldError"></div>
        `;
    }

    _wireFieldFormEvents(draft) {
        const $type = document.getElementById('fldType');
        const $typeSpecific = document.getElementById('fldTypeSpecific');

        const renderTypeSpecific = () => {
            const t = $type.value;
            if (t === 'select' || t === 'multi_select') {
                $typeSpecific.innerHTML = `
                    <label>Inline options (one "id|Label" per line — optional if using options_ref)
                        <textarea id="fldOptionsText" rows="4"
                            placeholder="value_a|Option A
value_b|Option B">${esc((draft.options || []).map(o =>
                                typeof o === 'object' ? `${o.id}|${o.label || o.id}` : String(o)
                            ).join('\n'))}</textarea>
                    </label>
                    <label>Or pull from lookup_data
                        <select id="fldOptionsRef">
                            <option value="">(none)</option>
                            ${Object.keys(this.schema.lookup_data || {}).map(r => `
                                <option value="${esc(r)}" ${draft.options_ref === r ? 'selected' : ''}>${esc(r)}</option>
                            `).join('')}
                        </select>
                    </label>
                `;
            } else if (t === 'lookup') {
                $typeSpecific.innerHTML = `
                    <label>Lookup reference (must exist in lookup_data)
                        <select id="fldLookupRef">
                            <option value="">(choose…)</option>
                            ${Object.keys(this.schema.lookup_data || {}).map(r => `
                                <option value="${esc(r)}" ${draft.lookup_ref === r ? 'selected' : ''}>${esc(r)}</option>
                            `).join('')}
                        </select>
                    </label>
                    <label>Display as
                        <select id="fldDisplayAs">
                            <option value="table" ${draft.display_as === 'table' ? 'selected' : ''}>table</option>
                            <option value="cards" ${draft.display_as === 'cards' ? 'selected' : ''}>cards</option>
                            <option value="list" ${draft.display_as === 'list' ? 'selected' : ''}>list</option>
                        </select>
                    </label>
                `;
            } else {
                $typeSpecific.innerHTML = '';
            }
        };
        renderTypeSpecific();
        $type.addEventListener('change', renderTypeSpecific);
    }

    _saveFieldModal() {
        if (!this._fieldEditCtx) return;
        const { sectionId, fieldId, draft } = this._fieldEditCtx;
        const section = (this.schema.sections || []).find(s => s.id === sectionId);
        if (!section) return;

        const newId = (document.getElementById('fldId').value || '').trim();
        const newLabel = (document.getElementById('fldLabel').value || '').trim();
        const newType = document.getElementById('fldType').value;
        const newRequired = document.getElementById('fldRequired').checked;
        const newHint = (document.getElementById('fldHint').value || '').trim();

        const $err = document.getElementById('fldError');
        if (!newId) { $err.textContent = 'Field ID is required.'; return; }
        if (!/^[a-zA-Z][a-zA-Z0-9_]*$/.test(newId)) {
            $err.textContent = 'Field ID must start with a letter; use letters/digits/underscores only.';
            return;
        }
        // Duplicate check (excluding self when editing)
        const dup = (section.fields || []).some(f => f.id === newId && f.id !== fieldId);
        if (dup) { $err.textContent = `Field "${newId}" already exists in this section.`; return; }

        // Parse validation/conditional JSON
        let validation = null;
        try {
            const txt = (document.getElementById('fldValidationJson').value || '').trim();
            validation = txt ? JSON.parse(txt) : null;
        } catch (e) { $err.textContent = 'Validation JSON is invalid: ' + e.message; return; }

        // If user picked a rule from the dropdown but left JSON empty, build from dropdown
        const ruleFromDropdown = document.getElementById('fldValidationRule').value;
        if ((!validation || !validation.rule) && ruleFromDropdown) {
            validation = Object.assign({}, validation || {}, { rule: ruleFromDropdown });
        }

        let conditional = null;
        try {
            const txt = (document.getElementById('fldConditional').value || '').trim();
            conditional = txt && txt !== '{}' ? JSON.parse(txt) : null;
        } catch (e) { $err.textContent = 'Conditional JSON is invalid: ' + e.message; return; }

        const newField = {
            id: newId,
            label: newLabel || newId,
            type: newType,
            required: !!newRequired,
        };
        if (newHint) newField.prompt_hint = newHint;
        if (validation && Object.keys(validation).length) newField.validation = validation;
        if (conditional && Object.keys(conditional).length) newField.conditional = conditional;

        // Type-specific
        if (newType === 'select' || newType === 'multi_select') {
            const optsText = (document.getElementById('fldOptionsText') || {}).value || '';
            const optsRef = (document.getElementById('fldOptionsRef') || {}).value || '';
            const inline = optsText.split('\n').map(line => line.trim()).filter(Boolean).map(line => {
                const [id, ...rest] = line.split('|');
                return { id: id.trim(), label: rest.join('|').trim() || id.trim() };
            });
            if (inline.length) newField.options = inline;
            if (optsRef) newField.options_ref = optsRef;
        }
        if (newType === 'lookup') {
            const ref = (document.getElementById('fldLookupRef') || {}).value || '';
            const display = (document.getElementById('fldDisplayAs') || {}).value || '';
            if (ref) newField.lookup_ref = ref;
            if (display) newField.display_as = display;
        }

        const fields = section.fields = section.fields || [];
        if (fieldId) {
            const idx = fields.findIndex(f => f.id === fieldId);
            if (idx === -1) fields.push(newField);
            else fields[idx] = newField;
        } else {
            fields.push(newField);
        }
        this._closeModal(this.$fieldModal);
        this._fieldEditCtx = null;
        this._emit();
    }

    _deleteField(sid, fid) {
        if (!confirm(`Delete field "${fid}"?`)) return;
        const section = (this.schema.sections || []).find(s => s.id === sid);
        if (!section) return;
        section.fields = (section.fields || []).filter(f => f.id !== fid);
        this._emit();
    }

    // ------------------------------------------------------------------
    // Action modal
    // ------------------------------------------------------------------
    _openActionModal(idx, defaultType) {
        const actions = ((this.schema.completion || {}).actions || []);
        const existing = (idx !== null && idx !== undefined) ? actions[idx] : null;
        const draft = existing
            ? JSON.parse(JSON.stringify(existing))
            : { type: defaultType || 'email', label: '' };
        this._actionEditCtx = { index: idx, draft };
        this.$actionModalTitle.textContent = existing ? `Edit ${draft.type} action` : `New ${draft.type} action`;
        this.$actionModalBody.innerHTML = this._renderActionForm(draft);
        this.$actionModal.style.display = 'flex';
    }

    _renderActionForm(draft) {
        const t = draft.type;
        const desc = ACTION_TYPE_DESCRIPTIONS[t] || '';
        const common = `
            <p class="dca-form-help">${esc(desc)}</p>
            <label>Label (shown in the progress UI)
                <input type="text" id="actLabel" value="${esc(draft.label || '')}">
            </label>
            <label>
                <input type="checkbox" id="actContinueOnError" ${draft.continue_on_error ? 'checked' : ''}>
                Continue pipeline on error
            </label>
        `;

        if (t === 'email') {
            const transport = draft.transport || 'smtp';
            // Default ON. Only `false` (explicit) disables the fallback.
            const fallback = draft.transport_fallback !== false;
            return common + `
                <label>Transport — how the email is delivered
                    <select id="actTransport">
                        <option value="smtp" ${transport === 'smtp' ? 'selected' : ''}>SMTP — your own mail server (cfg.SMTP_*)</option>
                        <option value="cloud_api" ${transport === 'cloud_api' ? 'selected' : ''}>Cloud API — platform's hosted email service</option>
                    </select>
                </label>
                <p class="dca-form-help" style="margin-top:-8px;font-size:0.78rem;color:var(--text-tertiary,#71717a)">
                    SMTP uses your platform's configured SMTP credentials. Cloud API uses the
                    platform's hosted email service (rate-limited per tenant).
                </p>
                <label>
                    <input type="checkbox" id="actTransportFallback" ${fallback ? 'checked' : ''}>
                    Fall back to the other transport if the chosen one isn't configured
                </label>
                <p class="dca-form-help" style="margin-top:-8px;font-size:0.78rem;color:var(--text-tertiary,#71717a)">
                    On by default. Turn off for strict-transport requirements (e.g.
                    compliance mandates SMTP only — a misconfiguration should hard-fail
                    instead of silently using the hosted relay).
                </p>
                <label>To addresses (comma-separated)
                    <input type="text" id="actTo" value="${esc((draft.to || []).join(', '))}">
                </label>
                <label>To from field (optional — pulls a recipient from a collected field)
                    <input type="text" id="actToFromField" value="${esc(draft.to_from_field || '')}">
                </label>
                <label>CC (comma-separated)
                    <input type="text" id="actCc" value="${esc((draft.cc || []).join(', '))}">
                </label>
                <label>CC from collected field (optional)
                    <input type="text" id="actCcFromField" value="${esc(draft.cc_from_field || '')}"
                        placeholder="e.g. submitter_email">
                </label>
                <label>Auto-CC the authenticated user (optional)
                    <select id="actCcFromIdentity">
                        <option value="" ${!draft.cc_from_identity ? 'selected' : ''}>(none)</option>
                        <option value="email" ${draft.cc_from_identity === 'email' ? 'selected' : ''}>email — CC the SAML / parent-system user's email</option>
                        <option value="user_email" ${draft.cc_from_identity === 'user_email' ? 'selected' : ''}>user_email — same as above</option>
                    </select>
                </label>
                <label>From address (defaults to platform's notification address)
                    <input type="text" id="actFromAddress" value="${esc(draft.from_address || '')}">
                </label>
                <label>From name
                    <input type="text" id="actFromName" value="${esc(draft.from_name || '')}">
                </label>
                <label>Subject template (supports {{field_id}})
                    <input type="text" id="actSubject" value="${esc(draft.subject_template || '')}">
                </label>
                <label>Body format
                    <select id="actBodyFormat">
                        <option value="auto_summary" ${draft.body_format === 'auto_summary' ? 'selected' : ''}>Auto-generated summary</option>
                        <option value="html" ${draft.body_format === 'html' ? 'selected' : ''}>Custom HTML (in body_html)</option>
                    </select>
                </label>
                <label>Custom HTML (used when body_format=html; supports {{field_id}})
                    <textarea id="actBodyHtml" rows="4">${esc(draft.body_html || '')}</textarea>
                </label>
                <label>
                    <input type="checkbox" id="actIncludeJsonAttachment" ${draft.include_json_attachment ? 'checked' : ''}>
                    Attach JSON of the full submission
                </label>
                <div class="dca-modal-error" id="actError"></div>
            `;
        }
        if (t === 'sms') {
            return common + `
                <label>To (phone number, E.164 — supports {{field_id}})
                    <input type="text" id="actSmsTo" value="${esc(draft.to || '')}"
                        placeholder="+15551234567 or {{followup_phone}}">
                </label>
                <label>Or pull number from a collected field
                    <input type="text" id="actSmsToFromField" value="${esc(draft.to_from_field || '')}"
                        placeholder="e.g. followup_phone">
                </label>
                <label>Message template (supports {{field_id}}, {{__summary__}})
                    <textarea id="actSmsMessage" rows="4"
                        placeholder="New request from {{submitter_name}}: {{topic_id.label}} on {{target_date}}.">${esc(draft.message_template || draft.message || '')}</textarea>
                </label>
                <p class="dca-form-help" style="font-size:0.78rem;color:var(--text-tertiary,#71717a)">
                    Sent via the platform's hosted SMS service (subject to tenant SMS limits).
                    Carriers truncate around 160 characters — keep messages short.
                </p>
                <div class="dca-modal-error" id="actError"></div>
            `;
        }
        if (t === 'workflow') {
            const workflows = (window.DCA_BUILDER_APP && window.DCA_BUILDER_APP.workflows) || [];
            const opts = workflows.map(w => `<option value="${w.id}" ${draft.workflow_id === w.id ? 'selected' : ''}>${esc(w.name)} (${w.id})</option>`).join('');
            return common + `
                <label>Workflow (pick from your saved workflows)
                    <select id="actWorkflowId">
                        <option value="">(choose by id below)</option>
                        ${opts}
                    </select>
                </label>
                <label>OR enter workflow_id manually
                    <input type="number" id="actWorkflowIdManual" value="${esc(draft.workflow_id || '')}">
                </label>
                <label>OR workflow_name (resolved at runtime)
                    <input type="text" id="actWorkflowName" value="${esc(draft.workflow_name || '')}">
                </label>
                <label>Variable mapping (JSON: workflow_var → template)
                    <textarea id="actVariableMapping" rows="6">${esc(JSON.stringify(draft.variable_mapping || {}, null, 2))}</textarea>
                </label>
                <label>
                    <input type="checkbox" id="actWaitForCompletion" ${draft.wait_for_completion ? 'checked' : ''}>
                    Wait for workflow completion (blocking)
                </label>
                <div class="dca-modal-error" id="actError"></div>
            `;
        }
        if (t === 'api') {
            return common + `
                <label>HTTP method
                    <select id="actMethod">
                        ${['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map(m =>
                            `<option value="${m}" ${(draft.method || 'POST') === m ? 'selected' : ''}>${m}</option>`
                        ).join('')}
                    </select>
                </label>
                <label>URL (supports {{field_id}}, {{__secret:KEY__}})
                    <input type="text" id="actUrl" value="${esc(draft.url || '')}">
                </label>
                <label>Headers JSON
                    <textarea id="actHeaders" rows="4">${esc(JSON.stringify(draft.headers || {}, null, 2))}</textarea>
                </label>
                <label>Body mapping JSON (arbitrary shape; supports {{...}})
                    <textarea id="actBodyMapping" rows="6">${esc(JSON.stringify(draft.body_mapping || {}, null, 2))}</textarea>
                </label>
                <label>Success status codes (comma-separated; default: 2xx)
                    <input type="text" id="actSuccessCodes" value="${esc((draft.success_status_codes || []).join(', '))}">
                </label>
                <label>Timeout (seconds)
                    <input type="number" id="actTimeout" value="${esc(draft.timeout_seconds || 30)}">
                </label>
                <div class="dca-modal-error" id="actError"></div>
            `;
        }
        if (t === 'webhook') {
            return common + `
                <label>Webhook URL
                    <input type="text" id="actUrl" value="${esc(draft.url || '')}">
                </label>
                <label>Headers JSON (optional)
                    <textarea id="actHeaders" rows="3">${esc(JSON.stringify(draft.headers || {}, null, 2))}</textarea>
                </label>
                <label>
                    <input type="checkbox" id="actIncludeMetadata" ${draft.include_metadata !== false ? 'checked' : ''}>
                    Include session metadata (session_id, config_id, user_id, ts)
                </label>
                <label>Timeout (seconds)
                    <input type="number" id="actTimeout" value="${esc(draft.timeout_seconds || 15)}">
                </label>
                <div class="dca-modal-error" id="actError"></div>
            `;
        }
        if (t === 'agent') {
            const agents = (window.DCA_BUILDER_APP && window.DCA_BUILDER_APP.agents) || [];
            const opts = agents.map(a => `<option value="${a.id}" ${draft.agent_id === a.id ? 'selected' : ''}>${esc(a.name)} (${a.id})</option>`).join('');
            return common + `
                <label>Target agent
                    <select id="actAgentId">
                        <option value="">(choose…)</option>
                        ${opts}
                    </select>
                </label>
                <label>Message template (supports {{field_id}}, {{__summary__}}, {{__all_data__}})
                    <textarea id="actMessageTemplate" rows="5">${esc(draft.message_template || '')}</textarea>
                </label>
                <label>
                    <input type="checkbox" id="actWaitForResponse" ${draft.wait_for_response ? 'checked' : ''}>
                    Wait for the agent's response (blocking)
                </label>
                <label>Timeout (seconds)
                    <input type="number" id="actTimeout" value="${esc(draft.timeout_seconds || 60)}">
                </label>
                <div class="dca-modal-error" id="actError"></div>
            `;
        }
        return common + `<p class="dca-help">No editor for action type "${esc(t)}".</p>`;
    }

    _saveActionModal() {
        if (!this._actionEditCtx) return;
        const { index, draft } = this._actionEditCtx;
        const t = draft.type;
        const $err = document.getElementById('actError');
        const result = { type: t, label: (document.getElementById('actLabel').value || '').trim() };
        result.continue_on_error = document.getElementById('actContinueOnError').checked;

        try {
            if (t === 'email') {
                const transport = (document.getElementById('actTransport').value || 'smtp').trim();
                if (transport && transport !== 'smtp') result.transport = transport;
                // Default 'smtp' is omitted to keep the schema JSON clean —
                // when reloading we treat absent as smtp.
                const fallback = document.getElementById('actTransportFallback').checked;
                // Only persist the field when it's the non-default (false).
                // Absent in the JSON means "fallback enabled" (the default).
                if (!fallback) result.transport_fallback = false;
                const to = (document.getElementById('actTo').value || '').split(',').map(s => s.trim()).filter(Boolean);
                if (to.length) result.to = to;
                const ttf = (document.getElementById('actToFromField').value || '').trim();
                if (ttf) result.to_from_field = ttf;
                const cc = (document.getElementById('actCc').value || '').split(',').map(s => s.trim()).filter(Boolean);
                if (cc.length) result.cc = cc;
                const ccff = (document.getElementById('actCcFromField').value || '').trim();
                if (ccff) result.cc_from_field = ccff;
                const ccfi = (document.getElementById('actCcFromIdentity').value || '').trim();
                if (ccfi) result.cc_from_identity = ccfi;
                const fa = (document.getElementById('actFromAddress').value || '').trim();
                if (fa) result.from_address = fa;
                const fn = (document.getElementById('actFromName').value || '').trim();
                if (fn) result.from_name = fn;
                result.subject_template = (document.getElementById('actSubject').value || '').trim();
                result.body_format = document.getElementById('actBodyFormat').value;
                const html = (document.getElementById('actBodyHtml').value || '').trim();
                if (html) result.body_html = html;
                result.include_json_attachment = document.getElementById('actIncludeJsonAttachment').checked;
            } else if (t === 'sms') {
                const to = (document.getElementById('actSmsTo').value || '').trim();
                if (to) result.to = to;
                const tff = (document.getElementById('actSmsToFromField').value || '').trim();
                if (tff) result.to_from_field = tff;
                const msg = (document.getElementById('actSmsMessage').value || '').trim();
                if (msg) result.message_template = msg;
            } else if (t === 'workflow') {
                const picked = document.getElementById('actWorkflowId').value;
                const manual = document.getElementById('actWorkflowIdManual').value;
                if (picked) result.workflow_id = parseInt(picked, 10);
                else if (manual) result.workflow_id = parseInt(manual, 10);
                const name = (document.getElementById('actWorkflowName').value || '').trim();
                if (name) result.workflow_name = name;
                const mapTxt = (document.getElementById('actVariableMapping').value || '').trim();
                result.variable_mapping = mapTxt ? JSON.parse(mapTxt) : {};
                result.wait_for_completion = document.getElementById('actWaitForCompletion').checked;
            } else if (t === 'api') {
                result.method = document.getElementById('actMethod').value;
                result.url = (document.getElementById('actUrl').value || '').trim();
                const hdrs = (document.getElementById('actHeaders').value || '').trim();
                result.headers = hdrs ? JSON.parse(hdrs) : {};
                const body = (document.getElementById('actBodyMapping').value || '').trim();
                result.body_mapping = body ? JSON.parse(body) : {};
                const codes = (document.getElementById('actSuccessCodes').value || '').split(',').map(s => parseInt(s.trim(), 10)).filter(n => !Number.isNaN(n));
                if (codes.length) result.success_status_codes = codes;
                result.timeout_seconds = parseInt(document.getElementById('actTimeout').value, 10) || 30;
            } else if (t === 'webhook') {
                result.url = (document.getElementById('actUrl').value || '').trim();
                const hdrs = (document.getElementById('actHeaders').value || '').trim();
                if (hdrs && hdrs !== '{}') result.headers = JSON.parse(hdrs);
                result.include_metadata = document.getElementById('actIncludeMetadata').checked;
                result.timeout_seconds = parseInt(document.getElementById('actTimeout').value, 10) || 15;
            } else if (t === 'agent') {
                const aid = document.getElementById('actAgentId').value;
                if (aid) result.agent_id = parseInt(aid, 10);
                result.message_template = (document.getElementById('actMessageTemplate').value || '').trim();
                result.wait_for_response = document.getElementById('actWaitForResponse').checked;
                result.timeout_seconds = parseInt(document.getElementById('actTimeout').value, 10) || 60;
            }
        } catch (e) {
            $err.textContent = 'JSON parse error: ' + e.message;
            return;
        }

        const completion = this.schema.completion = this.schema.completion || {};
        const actions = completion.actions = completion.actions || [];
        if (index === null || index === undefined) {
            actions.push(result);
        } else {
            actions[index] = result;
        }
        this._closeModal(this.$actionModal);
        this._actionEditCtx = null;
        this._emit();
    }

    _moveAction(idx, delta) {
        const actions = ((this.schema.completion || {}).actions || []);
        const target = idx + delta;
        if (target < 0 || target >= actions.length) return;
        const tmp = actions[idx];
        actions[idx] = actions[target];
        actions[target] = tmp;
        this._emit();
    }

    _deleteAction(idx) {
        if (!confirm('Delete this action?')) return;
        const actions = ((this.schema.completion || {}).actions || []);
        actions.splice(idx, 1);
        this._emit();
    }

    // ------------------------------------------------------------------
    // Lookup modal
    // ------------------------------------------------------------------
    _openLookupModal(ref) {
        const lookups = this.schema.lookup_data || {};
        const existing = ref ? lookups[ref] : null;
        const draft = existing
            ? JSON.parse(JSON.stringify(existing))
            : { source: 'inline', values: [] };
        this._lookupEditCtx = { ref: ref, draft };

        this.$lookupModalTitle.textContent = ref ? `Edit lookup: ${ref}` : 'Add lookup';
        const connOptions = this.connectionsList.map(c =>
            `<option value="${esc(c.id)}" ${draft.connection_id == c.id ? 'selected' : ''}>${esc(c.name)} — ${esc(c.database || '')} (${esc(c.type || '')})</option>`
        ).join('');

        this.$lookupModalBody.innerHTML = `
            <label>Reference name (snake_case — used as options_ref / lookup_ref)
                <input type="text" id="lookRef" value="${esc(ref || '')}" ${ref ? 'disabled' : ''}>
            </label>
            <label>Source
                <select id="lookSource">
                    <option value="inline" ${draft.source === 'inline' ? 'selected' : ''}>inline — values listed below (good for small static lists)</option>
                    <option value="file" ${draft.source === 'file' ? 'selected' : ''}>file — JSON in configs/&lt;file&gt;</option>
                    <option value="database" ${draft.source === 'database' ? 'selected' : ''}>database — query a SQL view via a saved connection</option>
                </select>
            </label>
            <div id="lookSourceFields"></div>
            <div class="dca-modal-error" id="lookError"></div>
        `;
        const renderSourceFields = () => {
            const src = document.getElementById('lookSource').value;
            const $body = document.getElementById('lookSourceFields');
            if (src === 'file') {
                $body.innerHTML = `
                    <label>File name (placed in configs/)
                        <input type="text" id="lookFile" value="${esc(draft.file || '')}">
                    </label>`;
            } else if (src === 'database') {
                $body.innerHTML = `
                    <p class="dca-form-help">
                        Query a SQL view through one of the platform's saved connections.
                        Only the columns you allowlist below are read; nothing else
                        reaches the AI. Filters with <code>{{collected.&lt;section&gt;.&lt;field&gt;}}</code>
                        let you scope results based on what the user has answered.
                    </p>
                    <label>Connection
                        <select id="lookConnId">
                            <option value="">(pick a saved connection)</option>
                            ${connOptions}
                        </select>
                    </label>
                    <label>View or table (schema.name OK; identifier-only — no inline SQL)
                        <input type="text" id="lookView" value="${esc(draft.view || '')}"
                            placeholder="vw_compliant_speakers">
                    </label>
                    <label>Select columns (comma-separated; allowlist — anything missing
                        from this list is never queried)
                        <div style="display:flex;gap:0.4rem;align-items:center;">
                            <input type="text" id="lookSelectColumns"
                                value="${esc((draft.select_columns || []).join(', '))}"
                                placeholder="speaker_id, name, tier"
                                style="flex:1;">
                            <button class="dca-btn-sm" type="button" id="lookFetchColumns" title="Fetch column names from the view">
                                <i class="fas fa-rotate"></i> Detect
                            </button>
                        </div>
                    </label>
                    <label>Filter rules (JSON: column[__op] → literal or {{collected.X}})
                        <textarea id="lookFilterBy" rows="5"
                            placeholder='{"products_certified__contains": "{{collected.basics.product}}", "active": true}'>${esc(JSON.stringify(draft.filter_by || {}, null, 2))}</textarea>
                    </label>
                    <label>Order-by (JSON: array of {column, direction})
                        <textarea id="lookOrderBy" rows="3"
                            placeholder='[{"column": "rating", "direction": "desc"}]'>${esc(JSON.stringify(draft.order_by || [], null, 2))}</textarea>
                    </label>
                    <label>Row cap (max rows fetched per query)
                        <input type="number" id="lookLimit" value="${esc(draft.limit || 200)}" min="1" max="2000">
                    </label>
                `;
                // Wire the column-detect button
                const $fetchBtn = document.getElementById('lookFetchColumns');
                if ($fetchBtn) {
                    $fetchBtn.addEventListener('click', async () => {
                        const cid = document.getElementById('lookConnId').value;
                        const view = (document.getElementById('lookView').value || '').trim();
                        if (!cid || !view) {
                            document.getElementById('lookError').textContent =
                                'Pick a connection and enter a view name first.';
                            return;
                        }
                        $fetchBtn.disabled = true;
                        $fetchBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
                        try {
                            const resp = await fetch(
                                `/api/data-collection/builder/connections/${cid}/columns?view=${encodeURIComponent(view)}`,
                                { credentials: 'same-origin' },
                            );
                            const body = await resp.json();
                            if (body.status === 'success' && body.columns && body.columns.length) {
                                document.getElementById('lookSelectColumns').value = body.columns.join(', ');
                                document.getElementById('lookError').textContent =
                                    `Detected ${body.columns.length} column(s). Edit the list to remove anything sensitive.`;
                            } else {
                                document.getElementById('lookError').textContent =
                                    body.warning || body.error || 'No columns returned.';
                            }
                        } catch (e) {
                            document.getElementById('lookError').textContent =
                                `Could not detect columns: ${e.message || e}`;
                        } finally {
                            $fetchBtn.disabled = false;
                            $fetchBtn.innerHTML = '<i class="fas fa-rotate"></i> Detect';
                        }
                    });
                }
            } else {
                $body.innerHTML = `
                    <label>Values JSON (array of {id, label, ...})
                        <textarea id="lookValues" rows="8"
                            placeholder='[{"id":"a","label":"A"}, {"id":"b","label":"B"}]'>${esc(JSON.stringify(draft.values || [], null, 2))}</textarea>
                    </label>`;
            }
        };
        renderSourceFields();
        document.getElementById('lookSource').addEventListener('change', renderSourceFields);

        this.$lookupModal.style.display = 'flex';
    }

    _saveLookupModal() {
        if (!this._lookupEditCtx) return;
        const { ref, draft } = this._lookupEditCtx;
        const $err = document.getElementById('lookError');
        const newRef = (document.getElementById('lookRef').value || ref || '').trim();
        const source = document.getElementById('lookSource').value;
        if (!newRef) { $err.textContent = 'Reference name is required.'; return; }
        if (!/^[a-zA-Z][a-zA-Z0-9_]*$/.test(newRef)) {
            $err.textContent = 'Reference must start with a letter; letters/digits/underscores only.';
            return;
        }
        const entry = { source };
        if (source === 'file') {
            entry.file = (document.getElementById('lookFile').value || '').trim();
            if (!entry.file) { $err.textContent = 'File name is required.'; return; }
        } else if (source === 'database') {
            const cid = parseInt(document.getElementById('lookConnId').value || '0', 10);
            const view = (document.getElementById('lookView').value || '').trim();
            const selRaw = (document.getElementById('lookSelectColumns').value || '').trim();
            if (!cid) { $err.textContent = 'Pick a connection.'; return; }
            if (!view) { $err.textContent = 'View / table name is required.'; return; }
            if (!/^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$/.test(view)) {
                $err.textContent = 'View name must be an identifier (schema.name or name only); no inline SQL.';
                return;
            }
            const cols = selRaw.split(',').map(s => s.trim()).filter(Boolean);
            if (!cols.length) { $err.textContent = 'At least one select column is required (it\'s the privacy allowlist).'; return; }
            const badCols = cols.filter(c => !/^[A-Za-z_][A-Za-z0-9_]*$/.test(c));
            if (badCols.length) {
                $err.textContent = `Invalid column name(s): ${badCols.join(', ')}`;
                return;
            }
            let filterBy = {};
            const filterRaw = (document.getElementById('lookFilterBy').value || '').trim();
            if (filterRaw) {
                try { filterBy = JSON.parse(filterRaw); }
                catch (e) { $err.textContent = 'Filter rules JSON is invalid: ' + e.message; return; }
                if (typeof filterBy !== 'object' || Array.isArray(filterBy) || !filterBy) {
                    $err.textContent = 'Filter rules must be a JSON object (key→value).';
                    return;
                }
            }
            let orderBy = [];
            const orderRaw = (document.getElementById('lookOrderBy').value || '').trim();
            if (orderRaw) {
                try { orderBy = JSON.parse(orderRaw); }
                catch (e) { $err.textContent = 'Order-by JSON is invalid: ' + e.message; return; }
                if (!Array.isArray(orderBy)) {
                    $err.textContent = 'Order-by must be a JSON array.';
                    return;
                }
            }
            const limit = parseInt(document.getElementById('lookLimit').value || '200', 10);
            entry.connection_id = cid;
            entry.view = view;
            entry.select_columns = cols;
            if (Object.keys(filterBy).length) entry.filter_by = filterBy;
            if (orderBy.length) entry.order_by = orderBy;
            if (limit && limit > 0) entry.limit = limit;
        } else {
            try {
                entry.values = JSON.parse(document.getElementById('lookValues').value || '[]');
            } catch (e) { $err.textContent = 'Values JSON is invalid: ' + e.message; return; }
            if (!Array.isArray(entry.values)) { $err.textContent = 'Values must be a JSON array.'; return; }
        }
        this.schema.lookup_data = this.schema.lookup_data || {};
        this.schema.lookup_data[newRef] = entry;
        this._closeModal(this.$lookupModal);
        this._lookupEditCtx = null;
        this._emit();
    }

    _deleteLookup(ref) {
        if (!confirm(`Delete lookup "${ref}"? Any field references will become invalid.`)) return;
        if (!this.schema.lookup_data) return;
        delete this.schema.lookup_data[ref];
        this._emit();
    }

    // ------------------------------------------------------------------
    _closeModal(modalEl) { modalEl.style.display = 'none'; }

    _emit() {
        try {
            this.onChange(this.schema);
        } catch (e) {
            console.error('[schema-editor] onChange threw:', e);
        }
    }
}

function esc(s) {
    if (s === null || s === undefined) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

window.SchemaEditor = SchemaEditor;
