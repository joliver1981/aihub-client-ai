// compliance_export_node.js
// Frontend module for the Compliance Excel Export workflow node.
// Handles the source-mode toggle and (when in "latest_in_set" mode)
// populates retailer + document-set dropdowns from the compliance API.
//
// Two modes:
//   - 'version'        : user passes a versionVariable resolving to a version_id
//   - 'latest_in_set'  : user picks a retailer + set; node resolves the current version

const ComplianceExportNode = {

    /**
     * Called by configureNode() when the modal opens for a Compliance Excel Export node.
     * @param {Object} currentConfig - existing node config (may be empty for new nodes)
     */
    init: function(currentConfig) {
        currentConfig = currentConfig || {};

        // Toggle Version / Latest-in-Set sections to match the saved sourceMode.
        const mode = currentConfig.sourceMode || 'version';
        const select = document.getElementById('cee-source-mode');
        if (select) {
            select.value = mode;
            this._toggleSections(mode);
        }

        // Only load retailers if we're in latest_in_set mode OR if a setId is configured
        // (preselect needs to walk retailers to find the parent of the saved set).
        if (mode === 'latest_in_set' || currentConfig.setId) {
            this._loadRetailers(currentConfig);
        }
    },

    onSourceModeChange: function(selectEl) {
        const mode = selectEl.value;
        this._toggleSections(mode);
        // Lazy-load retailers the first time the user switches to latest_in_set mode
        if (mode === 'latest_in_set') {
            const retSel = document.getElementById('cee-retailer');
            if (retSel && retSel.options.length <= 1) {
                this._loadRetailers({});
            }
        }
    },

    onRetailerChange: function(selectEl) {
        const retailerId = selectEl.value;
        const setSel = document.getElementById('cee-set');
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
     * Show / hide the version-variable section vs. the retailer/set section.
     */
    _toggleSections: function(mode) {
        const verSec = document.getElementById('cee-version-section');
        const setSec = document.getElementById('cee-set-section');
        if (verSec) verSec.style.display = (mode === 'version') ? '' : 'none';
        if (setSec) setSec.style.display = (mode === 'latest_in_set') ? '' : 'none';
    },

    /**
     * Fetch retailers from the compliance API and populate the dropdown.
     * If currentConfig has a setId, also pre-select the appropriate retailer
     * by walking sets to find the matching one.
     */
    _loadRetailers: function(currentConfig) {
        const sel = document.getElementById('cee-retailer');
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
                if (currentConfig && currentConfig.setId) {
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

        const tryNext = (i) => {
            if (i >= retailers.length) return;
            const rid = retailers[i].retailer_id;
            fetch(`/api/compliance/retailers/${rid}/sets`)
                .then(function(r) { return r.json(); })
                .then((data) => {
                    const sets = (data && data.sets) || [];
                    const match = sets.find(function(s) { return s.set_id === targetSetId; });
                    if (match) {
                        const retSel = document.getElementById('cee-retailer');
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
                const setSel = document.getElementById('cee-set');
                if (setSel) setSel.innerHTML = '<option value="">— failed to load —</option>';
            });
    },

    _populateSetsDropdown: function(sets, selectedSetId) {
        const setSel = document.getElementById('cee-set');
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
    }
};
