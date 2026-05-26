// compliance_process_node.js
// Frontend module for the Compliance Process workflow node.
// Loads retailers, sets, and accessible agents from the compliance API
// and wires the Fixed/Dynamic routing-mode UI.

const ComplianceProcessNode = {

    /**
     * Called by configureNode() when the modal opens for a Compliance Process node.
     * @param {Object} currentConfig - existing node config (may be empty for new nodes)
     */
    init: function(currentConfig) {
        currentConfig = currentConfig || {};

        // Toggle Fixed / Dynamic sections to match the saved routingMode.
        const mode = currentConfig.routingMode || 'fixed';
        const select = document.getElementById('cp-routing-mode');
        if (select) {
            select.value = mode;
            this._toggleSections(mode);
        }

        // Restore onMissing value and show/hide agent options accordingly.
        const onMissingSel = document.getElementById('cp-on-missing');
        if (onMissingSel && currentConfig.onMissing) {
            onMissingSel.value = currentConfig.onMissing;
        }

        // Restore agent auto-creation options.
        const autoCreateChk = document.getElementById('cp-auto-create-agent');
        if (autoCreateChk) {
            autoCreateChk.checked = !!currentConfig.autoCreateAgent;
        }
        const agentModeSel = document.getElementById('cp-agent-mode');
        if (agentModeSel && currentConfig.agentMode) {
            agentModeSel.value = currentConfig.agentMode;
        }
        const objTemplate = document.querySelector('textarea[name="agentObjectiveTemplate"]');
        if (objTemplate && currentConfig.agentObjectiveTemplate) {
            objTemplate.value = currentConfig.agentObjectiveTemplate;
        }

        // Show agent options if onMissing is auto_create, and the
        // retailer-agent-override row only when in per_retailer mode.
        this._toggleAgentOptions();
        this._toggleRetailerOverrideRow();

        // Populate retailers + agents in parallel.
        this._loadRetailers(currentConfig);
        this._loadAgents(currentConfig);
        this._loadRetailerOverrideAgents(currentConfig);
    },

    onRoutingModeChange: function(selectEl) {
        this._toggleSections(selectEl.value);
    },

    onRetailerChange: function(selectEl) {
        const retailerId = selectEl.value;
        // Clear set dropdown until we know the new retailer's sets
        const setSel = document.getElementById('cp-set');
        if (setSel) {
            setSel.innerHTML = '<option value="">Loading…</option>';
        }
        if (!retailerId) {
            if (setSel) setSel.innerHTML = '<option value="">Choose a retailer first</option>';
            return;
        }
        this._loadSets(retailerId, null);
    },

    /**
     * Show / hide the Fixed and Dynamic sections.
     */
    _toggleSections: function(mode) {
        const fixed = document.getElementById('cp-fixed-section');
        const dyn = document.getElementById('cp-dynamic-section');
        if (fixed) fixed.style.display = (mode === 'fixed') ? '' : 'none';
        if (dyn) dyn.style.display = (mode === 'dynamic') ? '' : 'none';
    },

    /**
     * Show / hide agent auto-creation options based on onMissing value.
     */
    _toggleAgentOptions: function() {
        const onMissingSel = document.getElementById('cp-on-missing');
        const agentOpts = document.getElementById('cp-agent-options');
        if (!agentOpts) return;
        const show = onMissingSel && onMissingSel.value === 'auto_create';
        agentOpts.style.display = show ? '' : 'none';
    },

    /**
     * Retailer Agent Override is only meaningful in per_retailer mode. Hide
     * the field in per_set mode to avoid confusion (per_set always creates
     * a dedicated agent per category, so there is no "retailer-level" slot
     * for the override to fill).
     */
    _toggleRetailerOverrideRow: function() {
        const modeSel = document.getElementById('cp-agent-mode');
        const row = document.getElementById('cp-retailer-agent-override-row');
        if (!row) return;
        const show = !modeSel || modeSel.value === 'per_retailer';
        row.style.display = show ? '' : 'none';
    },

    /**
     * Populate the Retailer Agent Override dropdown with accessible agents.
     * Reuses the same /api/compliance/accessible-agents endpoint as the
     * Agent Override field.
     */
    _loadRetailerOverrideAgents: function(currentConfig) {
        const sel = document.getElementById('cp-retailer-agent-override');
        if (!sel) return;
        fetch('/api/compliance/accessible-agents')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                const agents = (data && data.agents) || [];
                agents.forEach(function(a) {
                    const opt = document.createElement('option');
                    opt.value = a.agent_id;
                    opt.textContent = a.agent_name;
                    sel.appendChild(opt);
                });
                if (currentConfig.retailerAgentOverrideId) {
                    sel.value = currentConfig.retailerAgentOverrideId;
                }
            })
            .catch(function(err) {
                console.error('Failed to load retailer override agents:', err);
            });
    },

    /**
     * Fetch retailers from the compliance API and populate the dropdown.
     * If currentConfig has a setId, also pre-select the appropriate retailer
     * by walking sets to find the matching one.
     */
    _loadRetailers: function(currentConfig) {
        const sel = document.getElementById('cp-retailer');
        if (!sel) return;

        fetch('/api/compliance/retailers')
            .then(function(r) { return r.json(); })
            .then((data) => {
                sel.innerHTML = '<option value="">— Select retailer —</option>';
                const retailers = (data && data.retailers) || [];
                retailers.forEach(function(r) {
                    const opt = document.createElement('option');
                    opt.value = r.retailer_id;
                    opt.textContent = r.name;
                    sel.appendChild(opt);
                });

                // If a setId is already configured, find the parent retailer
                // and pre-select it, then load that retailer's sets.
                if (currentConfig.setId) {
                    this._preselectFromSetId(retailers, currentConfig);
                }
            })
            .catch(function(err) {
                console.error('Failed to load retailers:', err);
                sel.innerHTML = '<option value="">— failed to load —</option>';
            });
    },

    /**
     * Walk retailers' sets to find which one owns the configured setId,
     * then preselect that retailer and load its sets.
     */
    _preselectFromSetId: function(retailers, currentConfig) {
        const targetSetId = parseInt(currentConfig.setId, 10);
        if (!targetSetId) return;

        // Try retailers in sequence; stop on first match.
        const tryNext = (i) => {
            if (i >= retailers.length) return;
            const rid = retailers[i].retailer_id;
            fetch(`/api/compliance/retailers/${rid}/sets`)
                .then(function(r) { return r.json(); })
                .then((data) => {
                    const sets = (data && data.sets) || [];
                    const match = sets.find(function(s) { return s.set_id === targetSetId; });
                    if (match) {
                        const retSel = document.getElementById('cp-retailer');
                        if (retSel) retSel.value = rid;
                        this._populateSetsDropdown(sets, targetSetId);
                    } else {
                        tryNext(i + 1);
                    }
                })
                .catch(function() { tryNext(i + 1); });
        };
        tryNext(0);
    },

    /**
     * Load sets for a specific retailer.
     */
    _loadSets: function(retailerId, selectedSetId) {
        fetch(`/api/compliance/retailers/${retailerId}/sets`)
            .then(function(r) { return r.json(); })
            .then((data) => {
                const sets = (data && data.sets) || [];
                this._populateSetsDropdown(sets, selectedSetId);
            })
            .catch(function(err) {
                console.error('Failed to load sets:', err);
                const setSel = document.getElementById('cp-set');
                if (setSel) setSel.innerHTML = '<option value="">— failed to load —</option>';
            });
    },

    _populateSetsDropdown: function(sets, selectedSetId) {
        const setSel = document.getElementById('cp-set');
        if (!setSel) return;
        setSel.innerHTML = '<option value="">— Select set —</option>';
        sets.forEach(function(s) {
            const opt = document.createElement('option');
            opt.value = s.set_id;
            const agentLabel = s.agent_name ? ` (agent: ${s.agent_name})` : ' (no agent)';
            opt.textContent = s.category + agentLabel;
            setSel.appendChild(opt);
        });
        if (selectedSetId) {
            setSel.value = selectedSetId;
        }
    },

    /**
     * Populate the Agent Override dropdown using accessible (permission-filtered) agents.
     */
    _loadAgents: function(currentConfig) {
        const sel = document.getElementById('cp-agent-override');
        if (!sel) return;
        fetch('/api/compliance/accessible-agents')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                const agents = (data && data.agents) || [];
                agents.forEach(function(a) {
                    const opt = document.createElement('option');
                    opt.value = a.agent_id;
                    opt.textContent = a.agent_name;
                    sel.appendChild(opt);
                });
                if (currentConfig.agentOverrideId) {
                    sel.value = currentConfig.agentOverrideId;
                }
            })
            .catch(function(err) {
                console.error('Failed to load accessible agents:', err);
            });
    }
};
