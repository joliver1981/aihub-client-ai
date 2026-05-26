/**
 * dca-rich-renderer.js
 *
 * DCA-specific extension to the platform RichContentRenderer. Adds
 * block types tailored to data-collection workflows: info cards,
 * side-by-side option comparisons, field help panels, tip callouts,
 * and a polished recap panel.
 *
 * The platform renderer remains the source of truth for shared block
 * types (table, html_table, list, alert, etc.). We delegate to it for
 * anything we don't own.
 *
 * Usage:
 *     this.renderer = new DcaRichRenderer(new RichContentRenderer());
 *     const html = this.renderer.render({ blocks });
 *
 * Each new block's content shape:
 *
 *   info_card:
 *     { title, body, icon?, footnote?, kind?: 'info'|'tip'|'warning'|'success' }
 *
 *   option_detail:
 *     { id, label, description, icon?, examples?: [str], when_to_choose?: str,
 *       fields?: [{label, value}] }
 *
 *   comparison:
 *     { title?, options: [{ id, label, icon?, description?, examples?,
 *       when_to_choose?, fields?: [{label, value}] }] }
 *
 *   field_help:
 *     { field_label, field_type?, body, examples?: [str], tips?: [str],
 *       common_mistakes?: [str], required?: bool }
 *
 *   tip_callout:
 *     { title?, message, kind?: 'tip'|'info'|'warning'|'success' }
 *
 *   recap_panel:
 *     { sections: [{ id, title, rows: [{ label, value, field_id }] }],
 *       intro?, missing_required?: [{section, label}] }
 */

class DcaRichRenderer {
    constructor(platformRenderer) {
        this.platform = platformRenderer;
        this.dcaTypes = new Set([
            'info_card',
            'option_detail',
            'comparison',
            'field_help',
            'tip_callout',
            'recap_panel',
            // We render `table` ourselves rather than delegating to the
            // platform renderer. The platform's table styles are gated
            // on `.doc-page` / `.chat-page` / `.theme-dark` selectors
            // that don't match our `.dca-page` runtime, which made the
            // platform-rendered table invisible (light text on light
            // background, or default unstyled).
            'table',
        ]);
    }

    render(response) {
        if (!response) return '';
        // Accept the same shapes the platform renderer does
        let blocks = null;
        if (response.type === 'rich_content' && response.blocks) blocks = response.blocks;
        else if (Array.isArray(response.blocks)) blocks = response.blocks;
        if (!blocks) return this.platform.render(response);

        return blocks.map(b => this._renderBlock(b)).join('\n');
    }

    _renderBlock(block) {
        if (!block || !block.type) return '';
        if (this.dcaTypes.has(block.type)) {
            switch (block.type) {
                case 'info_card':     return this._renderInfoCard(block);
                case 'option_detail': return this._renderOptionDetail(block);
                case 'comparison':    return this._renderComparison(block);
                case 'field_help':    return this._renderFieldHelp(block);
                case 'tip_callout':   return this._renderTipCallout(block);
                case 'recap_panel':   return this._renderRecapPanel(block);
                case 'table':         return this._renderTable(block);
            }
        }
        // Unknown block type — fall through to the platform renderer
        // if it's available, otherwise log and render a visible
        // placeholder so the user sees SOMETHING and we get a clear
        // diagnostic instead of silent emptiness.
        if (!this.platform) {
            console.error('[DcaRichRenderer] no platform renderer available '
                + 'and no DCA handler for block.type=' + block.type
                + '. Block payload:', JSON.stringify(block).slice(0, 400));
            return `<div class="dca-rich" style="border:1px dashed #f87171;color:#fca5a5;padding:0.5rem;font-size:0.78rem;border-radius:6px;">`
                 + `Unknown block type: <code>${this._esc(block.type)}</code>`
                 + `</div>`;
        }
        // Defense in depth: the platform's `table` renderer requires
        // {headers, rows} on .content and `title` on .metadata. Older
        // backend code might emit {columns, rows, title} on .content.
        const adapted = this._adaptLegacyTable(block);
        try {
            const out = this.platform.render({ blocks: [adapted] });
            if (!out || !out.trim()) {
                console.warn('[DcaRichRenderer] platform returned empty for block:',
                    JSON.stringify(block).slice(0, 400));
            }
            return out || '';
        } catch (e) {
            console.error('[DcaRichRenderer] platform render threw for block:',
                JSON.stringify(block).slice(0, 400), e);
            return '';
        }
    }

    _adaptLegacyTable(block) {
        if (!block || block.type !== 'table' || !block.content) return block;
        const c = block.content;
        const hasNew = Array.isArray(c.headers) && Array.isArray(c.rows);
        const hasLegacy = Array.isArray(c.columns) && Array.isArray(c.rows);
        if (hasNew || !hasLegacy) return block;
        // Translate {columns, rows, title?} → {headers, rows} + metadata.title
        console.warn('[DcaRichRenderer] adapted legacy table shape '
            + '({columns} → {headers}). Backend should emit headers directly.');
        const adapted = {
            type: 'table',
            content: { headers: c.columns, rows: c.rows },
            metadata: Object.assign(
                {},
                block.metadata || {},
                c.title ? { title: c.title } : {},
            ),
        };
        return adapted;
    }

    // -----------------------------------------------------------------
    // Block renderers — return HTML strings
    // -----------------------------------------------------------------

    _renderInfoCard(block) {
        const c = block.content || {};
        const kind = c.kind || 'info';
        const icon = c.icon || this._defaultIcon(kind);
        return `
            <div class="dca-rich dca-info-card dca-kind-${this._esc(kind)}">
                <div class="dca-info-card-icon"><i class="fas ${this._esc(icon)}"></i></div>
                <div class="dca-info-card-body">
                    ${c.title ? `<div class="dca-info-card-title">${this._esc(c.title)}</div>` : ''}
                    <div class="dca-info-card-text">${this._md(c.body || '')}</div>
                    ${c.footnote ? `<div class="dca-info-card-footnote">${this._esc(c.footnote)}</div>` : ''}
                </div>
            </div>
        `;
    }

    _renderOptionDetail(block) {
        const c = block.content || {};
        const examples = (c.examples || []).map(e => `<li>${this._esc(e)}</li>`).join('');
        const fields = (c.fields || []).map(f =>
            `<div class="dca-od-field"><span>${this._esc(f.label)}</span><b>${this._esc(f.value)}</b></div>`
        ).join('');
        return `
            <div class="dca-rich dca-option-detail">
                <div class="dca-od-header">
                    ${c.icon ? `<i class="fas ${this._esc(c.icon)}"></i>` : ''}
                    <span class="dca-od-label">${this._esc(c.label || c.id || '')}</span>
                </div>
                ${c.description ? `<div class="dca-od-desc">${this._esc(c.description)}</div>` : ''}
                ${fields ? `<div class="dca-od-fields">${fields}</div>` : ''}
                ${c.when_to_choose ? `<div class="dca-od-when"><b>When to choose:</b> ${this._esc(c.when_to_choose)}</div>` : ''}
                ${examples ? `<div class="dca-od-examples"><b>Examples:</b><ul>${examples}</ul></div>` : ''}
            </div>
        `;
    }

    _renderComparison(block) {
        const c = block.content || {};
        const options = c.options || [];
        const cards = options.map(o => {
            const ex = (o.examples || []).map(e => `<li>${this._esc(e)}</li>`).join('');
            const fs = (o.fields || []).map(f =>
                `<div class="dca-od-field"><span>${this._esc(f.label)}</span><b>${this._esc(f.value)}</b></div>`
            ).join('');
            return `
                <div class="dca-cmp-card">
                    <div class="dca-cmp-card-header">
                        ${o.icon ? `<i class="fas ${this._esc(o.icon)}"></i>` : ''}
                        <span>${this._esc(o.label || o.id || '')}</span>
                    </div>
                    ${o.description ? `<div class="dca-cmp-desc">${this._esc(o.description)}</div>` : ''}
                    ${fs ? `<div class="dca-od-fields">${fs}</div>` : ''}
                    ${o.when_to_choose ? `<div class="dca-od-when"><b>When to choose:</b> ${this._esc(o.when_to_choose)}</div>` : ''}
                    ${ex ? `<div class="dca-od-examples"><b>Examples:</b><ul>${ex}</ul></div>` : ''}
                </div>
            `;
        }).join('');
        return `
            <div class="dca-rich dca-comparison">
                ${c.title ? `<div class="dca-comparison-title">${this._esc(c.title)}</div>` : ''}
                <div class="dca-comparison-grid">${cards}</div>
            </div>
        `;
    }

    _renderFieldHelp(block) {
        const c = block.content || {};
        const examples = (c.examples || []).map(e => `<li>${this._esc(e)}</li>`).join('');
        const tips = (c.tips || []).map(t => `<li>${this._esc(t)}</li>`).join('');
        const mistakes = (c.common_mistakes || []).map(m => `<li>${this._esc(m)}</li>`).join('');
        return `
            <div class="dca-rich dca-field-help">
                <div class="dca-fh-header">
                    <i class="fas fa-circle-info"></i>
                    <span class="dca-fh-label">${this._esc(c.field_label || 'Field help')}</span>
                    ${c.required ? '<span class="dca-fh-pill required">Required</span>' : '<span class="dca-fh-pill optional">Optional</span>'}
                    ${c.field_type ? `<span class="dca-fh-pill type">${this._esc(c.field_type)}</span>` : ''}
                </div>
                ${c.body ? `<div class="dca-fh-body">${this._md(c.body)}</div>` : ''}
                ${examples ? `<div class="dca-fh-section"><b>Examples:</b><ul>${examples}</ul></div>` : ''}
                ${tips ? `<div class="dca-fh-section"><b>Tips:</b><ul>${tips}</ul></div>` : ''}
                ${mistakes ? `<div class="dca-fh-section"><b>Avoid:</b><ul>${mistakes}</ul></div>` : ''}
            </div>
        `;
    }

    _renderTipCallout(block) {
        const c = block.content || {};
        const kind = c.kind || 'tip';
        const icon = this._defaultIcon(kind);
        return `
            <div class="dca-rich dca-tip-callout dca-kind-${this._esc(kind)}">
                <i class="fas ${this._esc(icon)}"></i>
                <div>
                    ${c.title ? `<div class="dca-tc-title">${this._esc(c.title)}</div>` : ''}
                    <div class="dca-tc-msg">${this._md(c.message || '')}</div>
                </div>
            </div>
        `;
    }

    _renderRecapPanel(block) {
        const c = block.content || {};
        const sections = (c.sections || []).map(s => {
            const rows = (s.rows || []).map(r => `
                <div class="dca-rp-row"
                     data-section-id="${this._esc(s.id)}"
                     data-field-id="${this._esc(r.field_id || '')}">
                    <span class="dca-rp-label">${this._esc(r.label)}</span>
                    <span class="dca-rp-value">${this._esc(r.value)}</span>
                    ${r.field_id ? `<button class="dca-rp-edit-btn"
                        data-section-id="${this._esc(s.id)}"
                        data-field-id="${this._esc(r.field_id)}"
                        title="Edit this">
                        <i class="fas fa-pen"></i></button>` : ''}
                </div>
            `).join('');
            return `
                <div class="dca-rp-section">
                    <div class="dca-rp-section-title">${this._esc(s.title || s.id || '')}</div>
                    <div class="dca-rp-rows">${rows || '<div class="dca-rp-empty">(nothing collected in this section)</div>'}</div>
                </div>
            `;
        }).join('');
        const missing = (c.missing_required || []).map(m =>
            `<li>${this._esc(m.section ? m.section + ' / ' : '')}${this._esc(m.label)}</li>`
        ).join('');
        return `
            <div class="dca-rich dca-recap-panel">
                <div class="dca-rp-header">
                    <i class="fas fa-clipboard-check"></i>
                    <span>Review your submission</span>
                </div>
                ${c.intro ? `<div class="dca-rp-intro">${this._esc(c.intro)}</div>` : ''}
                ${sections}
                ${missing ? `<div class="dca-rp-missing"><b>Still required:</b><ul>${missing}</ul></div>` : ''}
            </div>
        `;
    }

    _renderTable(block) {
        // Accepts both the platform shape ({headers, rows} on content)
        // and the legacy DCA shape ({columns, rows, title} on content).
        const c = block.content || {};
        const meta = block.metadata || {};
        const headers = c.headers || c.columns || [];
        const rows = c.rows || [];
        const title = meta.title || c.title || '';
        if (!Array.isArray(headers) || !Array.isArray(rows) || rows.length === 0) {
            return `<div class="dca-rich dca-table-empty">No data to display.</div>`;
        }
        const headerHtml = headers.map(h => `<th>${this._esc(h)}</th>`).join('');
        const rowsHtml = rows.map(row => {
            const cells = (Array.isArray(row) ? row : []).map(cell => {
                return `<td>${this._renderTableCell(cell)}</td>`;
            }).join('');
            return `<tr>${cells}</tr>`;
        }).join('');
        return `
            <div class="dca-rich dca-table">
                ${title ? `<div class="dca-table-title">${this._esc(title)}</div>` : ''}
                <table>
                    <thead><tr>${headerHtml}</tr></thead>
                    <tbody>${rowsHtml}</tbody>
                </table>
            </div>
        `;
    }

    /**
     * Render a single table cell. The previous version called
     * JSON.stringify on any object/array which produced ugly literal
     * JSON like  ["Routine ...","Standard ..."]  in the cell. This
     * version renders arrays as a readable bulleted list, dicts as
     * key: value pairs, and strings as plain text.
     */
    _renderTableCell(cell) {
        if (cell === null || cell === undefined) return '';
        if (typeof cell === 'string') return this._esc(cell);
        if (typeof cell === 'number' || typeof cell === 'boolean') {
            return this._esc(String(cell));
        }
        if (Array.isArray(cell)) {
            if (cell.length === 0) return '';
            // All-string arrays → bullet list (handles `examples` fields).
            // Mixed/nested → fall through to JSON for safety.
            const allScalar = cell.every(
                v => typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean'
            );
            if (allScalar) {
                return '<ul class="dca-table-cell-list">'
                    + cell.map(v => `<li>${this._esc(String(v))}</li>`).join('')
                    + '</ul>';
            }
            try { return this._esc(JSON.stringify(cell)); }
            catch (_) { return this._esc(String(cell)); }
        }
        if (typeof cell === 'object') {
            // Plain key: value pairs for objects with scalar values.
            const entries = Object.entries(cell);
            const allScalar = entries.every(
                ([, v]) => v === null || v === undefined ||
                           typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean'
            );
            if (allScalar) {
                return entries.map(
                    ([k, v]) => `<div class="dca-table-cell-kv">`
                        + `<b>${this._esc(k)}:</b> ${this._esc(String(v ?? ''))}`
                        + `</div>`
                ).join('');
            }
            try { return this._esc(JSON.stringify(cell)); }
            catch (_) { return this._esc(String(cell)); }
        }
        return this._esc(String(cell));
    }

    // -----------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------

    _defaultIcon(kind) {
        return ({
            info:    'fa-circle-info',
            tip:     'fa-lightbulb',
            warning: 'fa-triangle-exclamation',
            success: 'fa-circle-check',
        })[kind] || 'fa-circle-info';
    }

    _esc(s) {
        if (s === null || s === undefined) return '';
        return String(s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    // Light markdown — only the safe subset (bold, italic, paragraphs).
    // Inputs come from the AI / schema authors, so we don't allow raw HTML.
    _md(s) {
        if (s === null || s === undefined) return '';
        let t = this._esc(String(s));
        t = t.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
        t = t.replace(/\*([^*]+)\*/g, '<i>$1</i>');
        t = t.replace(/\n\n+/g, '</p><p>');
        return `<p>${t}</p>`;
    }
}

if (typeof window !== 'undefined') {
    window.DcaRichRenderer = DcaRichRenderer;
}
