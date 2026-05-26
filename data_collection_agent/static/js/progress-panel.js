/**
 * progress-panel.js
 *
 * Right-side progress tracker for the Data Collection Agent runtime page.
 *
 * Owns the rendering and interaction of the section/field progress display:
 *   - Sections as collapsible accordions, each labeled by status
 *   - Per-field rows showing label + collected value
 *   - Click a section header → navigate to that section (back/edit flow)
 *   - Click a field value → inline edit
 *
 * Surfaces three callbacks via the constructor opts:
 *   - onNavigate(sectionId)   — user clicked a section header
 *   - onEditField(sectionId, fieldId, fieldDef, currentValue)  — user clicked a field
 *
 * The DataCollectionApp owns sessionId / API plumbing; this class is dumb DOM.
 */

class ProgressPanel {
    /**
     * @param {object} schema   - The form schema (from /api/data-collection/schema/<id>)
     * @param {HTMLElement} listEl  - The container <div> to render sections into
     * @param {object} opts     - { onNavigate, onEditField }
     */
    constructor(schema, listEl, opts = {}) {
        this.schema = schema;
        this.listEl = listEl;
        this.onNavigate = opts.onNavigate || (() => {});
        this.onEditField = opts.onEditField || (() => {});

        // Track which sections the user has manually collapsed/expanded
        this.collapsed = {};
    }

    /**
     * Render or re-render the panel based on the current session state.
     *
     * @param {object} state {
     *   section_status: {section_id: status},
     *   collected_data: {section_id: {field_id: value}},
     *   current_section: section_id,
     *   validation_errors: {section_id: {field_id: [msg, ...]}}
     * }
     */
    render(state) {
        const sections = (this.schema.sections || [])
            .slice()
            .sort((a, b) => (a.order || 999) - (b.order || 999));

        // Build the HTML for all sections, then write at once
        const html = sections.map(section => this._renderSection(section, state)).join('');
        this.listEl.innerHTML = html;
        this._wireSectionEvents();
    }

    /**
     * Render one section as a collapsible card.
     */
    _renderSection(section, state) {
        const sid = section.id;
        const status = (state.section_status && state.section_status[sid]) || 'not_started';
        const isCurrent = state.current_section === sid;
        const isCollapsed = this._shouldCollapse(sid, status, isCurrent);

        // Filter out conditionally-hidden fields based on the
        // server-supplied visible_fields map (computed from the
        // schema's conditional.show_when rules against current data).
        // If the metadata doesn't include visible_fields (e.g. on the
        // first paint before any agent turn), default to showing all
        // — better to show too much briefly than to hide something
        // important.
        const allFields = section.fields || [];
        const visibleMap = state.visible_fields || null;
        const visibleIdsForSection = (visibleMap && visibleMap[sid]) || null;
        const fields = visibleIdsForSection
            ? allFields.filter(f => visibleIdsForSection.includes(f.id))
            : allFields;
        const errors = (state.validation_errors && state.validation_errors[sid]) || {};
        const sectionData = (state.collected_data && state.collected_data[sid]) || {};

        // not_started: empty circle, in_progress: pen ("you're filling this out"),
        // complete: check.  Avoids the loading-spinner connotation.
        const statusIcon = {
            'not_started': '<i class="far fa-circle"></i>',
            'in_progress': '<i class="fas fa-pen"></i>',
            'complete': '<i class="fas fa-check-circle"></i>',
        }[status] || '<i class="far fa-circle"></i>';

        const fieldRows = fields.map(field => {
            const fid = field.id;
            const value = sectionData[fid];
            const fieldErrs = errors[fid] || [];
            return this._renderFieldRow(sid, field, value, fieldErrs);
        }).join('');

        return `
            <div class="dca-section ${status} ${isCollapsed ? 'collapsed' : ''}" data-sid="${sid}">
                <div class="dca-section-header">
                    <span class="dca-section-status-icon">${statusIcon}</span>
                    <span class="dca-section-title">${this._escape(section.title || sid)}</span>
                    <button class="dca-section-edit-btn"
                            data-action="navigate"
                            data-sid="${sid}"
                            title="Go back to this section">
                        <i class="fas fa-edit"></i>
                    </button>
                </div>
                <div class="dca-section-body">
                    ${fieldRows || '<div class="dca-field-row"><span class="dca-field-label">(no fields collected)</span></div>'}
                </div>
            </div>
        `;
    }

    _renderFieldRow(sectionId, field, value, fieldErrors) {
        const fid = field.id;
        const isMissing = field.required && (value === undefined || value === null || value === '');
        const hasErr = fieldErrors && fieldErrors.length > 0;

        let displayValue;
        if (isMissing) {
            displayValue = `<span class="dca-field-value empty dca-field-required-missing">required</span>`;
        } else if (value === undefined || value === null || value === '') {
            displayValue = `<span class="dca-field-value empty">—</span>`;
        } else {
            displayValue = `<span class="dca-field-value ${hasErr ? 'error' : ''}">${this._escape(this._formatValue(value, field))}</span>`;
        }

        return `
            <div class="dca-field-row" data-action="edit-field"
                 data-sid="${sectionId}" data-fid="${fid}">
                <span class="dca-field-label">${this._escape(field.label || fid)}</span>
                ${displayValue}
            </div>
        `;
    }

    _formatValue(value, field) {
        if (value === null || value === undefined) return '';
        if (field.type === 'boolean') return value ? 'Yes' : 'No';
        if (field.type === 'multi_select' && Array.isArray(value)) {
            return value.join(', ');
        }
        // For lookup / select, try to look up the label from the schema's lookup_data
        if ((field.type === 'lookup' && field.lookup_ref) ||
            (field.type === 'select' && field.options_ref)) {
            const ref = field.lookup_ref || field.options_ref;
            const lookup = this.schema.lookup_data && this.schema.lookup_data[ref];
            const items = (lookup && lookup.values) || [];
            const match = items.find(it => it && typeof it === 'object' && String(it.id) === String(value));
            if (match) return String(match.label || match.name || value);
        }
        if (typeof value === 'object') return JSON.stringify(value);
        return String(value);
    }

    /**
     * Decide whether to collapse a section by default.
     *  - Current section: always expanded (unless user manually collapsed)
     *  - Complete sections: collapsed (unless user expanded)
     *  - Not started: collapsed
     */
    _shouldCollapse(sid, status, isCurrent) {
        if (this.collapsed[sid] !== undefined) return this.collapsed[sid];
        if (isCurrent) return false;
        return status !== 'in_progress';
    }

    _wireSectionEvents() {
        const sections = this.listEl.querySelectorAll('.dca-section');
        sections.forEach(sectionEl => {
            const sid = sectionEl.dataset.sid;
            const header = sectionEl.querySelector('.dca-section-header');

            header.addEventListener('click', (e) => {
                // If they clicked the edit button, route to navigate instead of toggling
                const action = e.target.closest('[data-action]');
                if (action && action.dataset.action === 'navigate') {
                    e.stopPropagation();
                    this.onNavigate(action.dataset.sid);
                    return;
                }
                // Otherwise toggle collapse
                sectionEl.classList.toggle('collapsed');
                this.collapsed[sid] = sectionEl.classList.contains('collapsed');
            });

            sectionEl.querySelectorAll('[data-action="edit-field"]').forEach(row => {
                row.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const sectionId = row.dataset.sid;
                    const fieldId = row.dataset.fid;
                    const fieldDef = this._findField(sectionId, fieldId);
                    if (!fieldDef) return;
                    const currentValue = this._currentValue(sectionId, fieldId);
                    this.onEditField(sectionId, fieldId, fieldDef, currentValue);
                });
            });
        });
    }

    _findField(sectionId, fieldId) {
        const section = (this.schema.sections || []).find(s => s.id === sectionId);
        if (!section) return null;
        return (section.fields || []).find(f => f.id === fieldId) || null;
    }

    _currentValue(sectionId, fieldId) {
        // The latest state is held by the app; we read from data-attrs set by render
        // Fall back to undefined (the app passes currentValue via onEditField anyway)
        return undefined;
    }

    /**
     * Compute completion percentage:
     * percent of required fields across the schema that have a non-empty value.
     */
    static computePercent(schema, collectedData) {
        let totalRequired = 0;
        let filledRequired = 0;
        (schema.sections || []).forEach(section => {
            (section.fields || []).forEach(field => {
                if (!field.required) return;
                totalRequired += 1;
                const value = (collectedData[section.id] || {})[field.id];
                if (value !== undefined && value !== null && value !== '' &&
                    !(Array.isArray(value) && value.length === 0)) {
                    filledRequired += 1;
                }
            });
        });
        if (totalRequired === 0) return 100;
        return Math.round((filledRequired / totalRequired) * 100);
    }

    static computeSectionCounts(schema, sectionStatus) {
        const total = (schema.sections || []).length;
        const complete = (schema.sections || []).filter(
            s => sectionStatus[s.id] === 'complete'
        ).length;
        return { total, complete };
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

window.ProgressPanel = ProgressPanel;
