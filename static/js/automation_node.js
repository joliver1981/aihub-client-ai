/**
 * Automation workflow-node config UX (james 2026-07-21): pick the automation
 * from a DROPDOWN (no GUID guessing) and get the automation's declared inputs
 * rendered as individual fields with their defaults as placeholders (no raw
 * JSON authoring). Values are stored in the node config exactly as before
 * (automationId/automationName + inputs JSON), so the engine contract is
 * unchanged and nodes configured by hand/API still work.
 *
 * The dynamic fields carry NO name attribute on purpose — the designer's
 * generic saveNodeConfig collector harvests named fields only, and
 * AutomationNode.getConfig() supplies these values instead (same pattern as
 * AIExtractNode / ExcelExportNode).
 */
const AutomationNode = (function () {
    let manifest = null;

    function _esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    async function setup(currentConfig) {
        const sel = document.getElementById('autoNodeSelect');
        if (!sel) return;
        const cur = currentConfig || {};
        sel.innerHTML = '<option value="">Loading automations…</option>';
        try {
            const r = await fetch('/automations/api/list');
            const d = await r.json();
            const autos = d.automations || [];
            sel.innerHTML = '<option value="">— choose an automation —</option>' +
                autos.map(a =>
                    `<option value="${_esc(a.automation_id)}" data-name="${_esc(a.name)}">` +
                    `${_esc(a.name)} ${a.pinned_version ? '(live v' + a.pinned_version + ')' : '(NOT promoted yet)'}` +
                    `</option>`).join('');
            let target = cur.automationId || '';
            if (!target && cur.automationName) {
                const m = autos.find(a => (a.name || '').toLowerCase() === String(cur.automationName).toLowerCase());
                if (m) target = m.automation_id;
            }
            if (target) {
                sel.value = target;
                await _renderInputs(cur);
            }
        } catch (e) {
            sel.innerHTML = `<option value="">could not load automations — are you signed in as a Developer?</option>`;
        }
        sel.onchange = () => _renderInputs(cur);
    }

    async function _renderInputs(cur) {
        const sel = document.getElementById('autoNodeSelect');
        const box = document.getElementById('autoNodeInputs');
        const meta = document.getElementById('autoNodeMeta');
        manifest = null;
        if (!sel || !box) return;
        if (!sel.value) {
            box.innerHTML = '<div class="text-muted small">Choose an automation to see its inputs.</div>';
            if (meta) meta.textContent = '';
            return;
        }
        box.innerHTML = '<div class="text-muted small">Loading inputs…</div>';
        try {
            const r = await fetch('/automations/api/' + encodeURIComponent(sel.value));
            const d = await r.json();
            manifest = (d.automation || {}).manifest || {};
            let existing = {};
            try {
                existing = typeof cur.inputs === 'string'
                    ? JSON.parse(cur.inputs || '{}') : (cur.inputs || {});
            } catch (e) { existing = {}; }
            const inputs = manifest.inputs || [];
            box.innerHTML = inputs.length
                ? inputs.map(inp => `
                    <div class="row mb-1 align-items-center">
                        <div class="col-4 small"><code>${_esc(inp.name)}</code></div>
                        <div class="col-8">
                            <input class="form-control form-control-sm auto-node-input"
                                data-name="${_esc(inp.name)}"
                                placeholder="default: ${_esc(inp.default == null ? '' : inp.default)}"
                                value="${_esc(existing[inp.name] != null ? existing[inp.name] : '')}">
                        </div>
                    </div>`).join('')
                : '<div class="text-muted small">This automation declares no inputs.</div>';
            if (meta) {
                meta.textContent = `uses — connections: ${(manifest.connections || []).join(', ') || 'none'}`
                    + ` · secrets: ${(manifest.secrets || []).join(', ') || 'none'}`
                    + ` · leave a field empty to use its default`;
            }
        } catch (e) {
            box.innerHTML = '<div class="text-danger small">Could not load this automation\'s inputs.</div>';
        }
    }

    function getConfig() {
        const sel = document.getElementById('autoNodeSelect');
        const opt = sel && sel.selectedOptions && sel.selectedOptions[0];
        const overrides = {};
        document.querySelectorAll('.auto-node-input').forEach(el => {
            const v = el.value;
            if (v != null && String(v).trim() !== '') overrides[el.getAttribute('data-name')] = v;
        });
        return {
            automationId: sel ? sel.value : '',
            automationName: opt ? (opt.getAttribute('data-name') || '') : '',
            inputs: Object.keys(overrides).length ? JSON.stringify(overrides) : ''
        };
    }

    return { setup, getConfig };
})();
