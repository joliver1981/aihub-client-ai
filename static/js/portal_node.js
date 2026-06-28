// portal_node.js
// Portal Node support for Workflow Designer
// Runs a SAVED Command Center portal workflow as a backend workflow step and
// exposes the downloaded file paths (and a result object) to downstream nodes.
//
// Modeled closely on integration_node.js. Loaded after workflow.js so that the
// shared globals (nodeConfigTemplates, nodeConfigs, configuredNode) already exist.
//
// Backend contract (workflow_execution.py -> _execute_portal_node) reads these
// node `config` keys:
//   portalWorkflowSlug  (string)  - which saved portal workflow to run
//   outputVariable      (string)  - var to receive {status,file_count,files,final_result,error}
//   filesVariable       (string)  - var to receive the bare list of downloaded file paths
//   uploadFilesVariable (string)  - name of a var holding file path(s) to UPLOAD (supports ${var})
//   timeout             (int sec, default 1200)
//   continueOnError     (bool, default false)
//   agentFallback       (bool, default true)
//   ownerUserId         - stamped SERVER-SIDE at save time (hidden passthrough only)

// ============================================
// Portal Node Configuration Template
// ============================================
nodeConfigTemplates['Portal'] = {
    template: `
        <div class="portal-config">
            <!-- Saved Portal Workflow selector -->
            <div class="mb-3">
                <label class="form-label fw-bold d-flex align-items-center" style="gap:8px;">
                    <i class="bi bi-box-arrow-in-right me-1"></i>Portal Workflow <span class="text-danger">*</span>
                    <button type="button" class="btn btn-sm btn-outline-secondary"
                            onclick="refreshPortalWorkflows()" title="Re-fetch saved portal workflows"
                            style="font-size:0.75rem;padding:2px 8px;margin-left:auto;">
                        <i class="bi bi-arrow-clockwise"></i> Refresh
                    </button>
                </label>
                <select class="form-control" name="portalWorkflowSlug" id="portal-workflow-selector">
                    <option value="">Loading portal workflows...</option>
                </select>
                <small class="form-text text-muted">
                    Pick a SAVED portal workflow.
                    <a href="/portal-workflows" target="_blank">
                        <i class="bi bi-plus-circle"></i> Create / manage portal workflows
                    </a>
                </small>
                <div id="portal-workflow-meta" class="form-text text-muted" style="display:none;"></div>
            </div>

            <hr class="my-3">

            <!-- Upload-files variable (feed documents INTO the portal workflow) -->
            <div class="mb-3">
                <label class="form-label fw-bold">
                    <i class="bi bi-cloud-upload me-1"></i>Upload Files Variable
                </label>
                <div class="input-group">
                    <input type="text" class="form-control" name="uploadFilesVariable" id="portal-upload-files-var"
                           placeholder="e.g. filesToUpload (or \${filesToUpload})" list="portal-var-list">
                </div>
                <small class="form-text text-muted">
                    Optional. A workflow variable holding file path(s) to upload into the portal workflow.
                    Accepts a bare name or the <code>\${var}</code> form.
                </small>
            </div>

            <hr class="my-3">

            <!-- Output object variable -->
            <div class="mb-3">
                <label class="form-label fw-bold">
                    <i class="bi bi-box-arrow-right me-1"></i>Output Variable
                </label>
                <div class="input-group">
                    <input type="text" class="form-control" name="outputVariable" id="portal-output-var"
                           placeholder="portalResult" list="portal-var-list">
                </div>
                <small class="form-text text-muted">
                    Receives an object: <code>{status, file_count, files, final_result, error}</code>
                </small>
            </div>

            <!-- Files list variable -->
            <div class="mb-3">
                <label class="form-label fw-bold">
                    <i class="bi bi-files me-1"></i>Files Variable
                </label>
                <div class="input-group">
                    <input type="text" class="form-control" name="filesVariable" id="portal-files-var"
                           placeholder="downloadedFiles" list="portal-var-list">
                </div>
                <small class="form-text text-muted">Receives the bare list of downloaded file paths</small>
            </div>

            <!-- Shared datalist of workflow variables for the inputs above -->
            <datalist id="portal-var-list"></datalist>

            <hr class="my-3">

            <!-- Timeout -->
            <div class="mb-3">
                <label class="form-label fw-bold">
                    <i class="bi bi-clock me-1"></i>Timeout (seconds)
                </label>
                <input type="number" class="form-control" name="timeout" id="portal-timeout"
                       min="1" value="1200" placeholder="1200">
                <small class="form-text text-muted">Maximum time to wait for the portal workflow to finish</small>
            </div>

            <!-- Agent fallback -->
            <div class="mb-3">
                <div class="form-check">
                    <input type="checkbox" class="form-check-input" name="agentFallback" id="portal-agent-fallback" checked>
                    <label class="form-check-label" for="portal-agent-fallback">
                        <strong>Agent fallback</strong>
                    </label>
                </div>
                <small class="form-text text-muted">Let a supervising agent step in if a recorded step gets stuck</small>
            </div>

            <!-- Error handling -->
            <div class="mb-3">
                <div class="form-check">
                    <input type="checkbox" class="form-check-input" name="continueOnError" id="portal-continue-on-error">
                    <label class="form-check-label" for="portal-continue-on-error">
                        <strong>Continue workflow on error</strong>
                    </label>
                </div>
                <small class="form-text text-muted">If unchecked, the workflow stops if the portal step fails</small>
            </div>

            <!-- Hidden passthrough: ownerUserId is stamped SERVER-SIDE at save time.
                 We keep the key present so it round-trips cleanly, but never expose
                 it as a user-facing field. -->
            <input type="hidden" name="ownerUserId" id="portal-owner-user-id" value="">
        </div>
    `,
    defaultConfig: {
        portalWorkflowSlug: '',
        uploadFilesVariable: '',
        outputVariable: '',
        filesVariable: '',
        timeout: 1200,
        agentFallback: true,
        continueOnError: false,
        ownerUserId: ''
    }
};

// ============================================
// Portal Node Helper Functions
// ============================================

// Cache for saved portal workflows (refresh periodically)
let portalWorkflowCache = {
    workflows: null,
    lastFetch: null
};

/**
 * Small HTML-escaping helper (the original used `portalEsc`).
 */
function portalEsc(s) {
    const div = document.createElement('div');
    div.textContent = s == null ? '' : String(s);
    return div.innerHTML;
}

/**
 * Load the saved portal workflows for the current user.
 * GET /api/portal-workflows -> { workflows: [{slug, name, step_count, uploads, ...}] }
 * Defensive: returns [] on any error or empty response.
 */
async function loadPortalWorkflowsForWorkflow(forceRefresh) {
    try {
        // 60s TTL — fresh enough that newly-saved portal workflows show up
        // quickly without re-fetching on every panel open.
        const CACHE_TTL_MS = 60000;
        if (!forceRefresh && portalWorkflowCache.workflows &&
            portalWorkflowCache.lastFetch &&
            (Date.now() - portalWorkflowCache.lastFetch) < CACHE_TTL_MS) {
            return portalWorkflowCache.workflows;
        }

        const response = await fetch(`/api/portal-workflows?_=${Date.now()}`);
        const data = await response.json();
        const workflows = (data && Array.isArray(data.workflows)) ? data.workflows : [];

        portalWorkflowCache.workflows = workflows;
        portalWorkflowCache.lastFetch = Date.now();
        return workflows;
    } catch (error) {
        console.error('Error loading portal workflows:', error);
        return [];
    }
}

/**
 * Public helper: force-refresh the saved portal workflow list. Called by the
 * small refresh button next to the Portal Workflow dropdown.
 */
window.refreshPortalWorkflows = async function() {
    const sel = document.getElementById('portal-workflow-selector');
    const keepValue = sel ? sel.value : '';
    portalWorkflowCache.workflows = null;
    portalWorkflowCache.lastFetch = null;
    await populatePortalWorkflowSelector(keepValue);
};

/**
 * Populate the portal workflow <select>. Restores `selectedSlug` if present.
 * Defensive: shows a friendly empty-state when no workflows exist.
 */
async function populatePortalWorkflowSelector(selectedSlug) {
    const selector = document.getElementById('portal-workflow-selector');
    if (!selector) return;

    selector.innerHTML = '<option value="">Loading portal workflows...</option>';

    const workflows = await loadPortalWorkflowsForWorkflow();

    if (!workflows.length) {
        selector.innerHTML = '<option value="">No saved portal workflows found</option>';
        updatePortalWorkflowMeta(null);
        return;
    }

    selector.innerHTML = '<option value="">Select a portal workflow...</option>';
    workflows.forEach(wf => {
        const option = document.createElement('option');
        option.value = wf.slug;
        const stepCount = (wf.step_count != null) ? wf.step_count : 0;
        option.textContent = `${wf.name || wf.slug} (${stepCount} steps)`;
        // Stash metadata so the meta line can reflect the selected workflow.
        option.dataset.uploads = wf.uploads ? '1' : '';
        option.dataset.stepCount = String(stepCount);
        selector.appendChild(option);
    });

    if (selectedSlug) {
        // If the saved slug is no longer in the list, add a disabled placeholder
        // so the user can see what was configured (rather than silently losing it).
        if (!Array.from(selector.options).some(o => o.value === selectedSlug)) {
            const missing = document.createElement('option');
            missing.value = selectedSlug;
            missing.textContent = `${selectedSlug} (not found — re-select)`;
            selector.appendChild(missing);
        }
        selector.value = selectedSlug;
    }

    updatePortalWorkflowMeta(selector.options[selector.selectedIndex] || null);
}

/**
 * Update the small meta line under the selector (does this workflow upload?).
 */
function updatePortalWorkflowMeta(option) {
    const meta = document.getElementById('portal-workflow-meta');
    if (!meta) return;
    if (!option || !option.value) {
        meta.style.display = 'none';
        meta.innerHTML = '';
        return;
    }
    const uploads = option.dataset.uploads === '1';
    meta.innerHTML = uploads
        ? '<i class="bi bi-info-circle"></i> This workflow uploads files — set an Upload Files Variable above to feed it documents.'
        : '<i class="bi bi-info-circle"></i> This workflow does not upload files.';
    meta.style.display = '';
}

/**
 * Populate the shared datalist with available workflow variables.
 */
function populatePortalVariableDatalist() {
    const datalist = document.getElementById('portal-var-list');
    if (!datalist) return;
    datalist.innerHTML = '';
    if (typeof workflowVariableDefinitions !== 'undefined' && workflowVariableDefinitions) {
        Object.keys(workflowVariableDefinitions).forEach(varName => {
            const option = document.createElement('option');
            option.value = varName;
            datalist.appendChild(option);
        });
    }
}

/**
 * Initialize Portal node config panel when opened.
 * Called by the workflow designer when the config modal is shown.
 */
async function initPortalConfigPanel(existingConfig) {
    // Populate the saved-workflow dropdown (restoring the saved slug if editing).
    await populatePortalWorkflowSelector(existingConfig && existingConfig.portalWorkflowSlug);

    // Wire the selector change -> meta line.
    const selector = document.getElementById('portal-workflow-selector');
    if (selector) {
        selector.addEventListener('change', function() {
            updatePortalWorkflowMeta(selector.options[selector.selectedIndex] || null);
        });
    }

    // Restore the remaining fields from existing config (the generic
    // configureNode() loop also sets these by `name`, but we set them
    // explicitly so the panel is correct even if that loop changes).
    if (existingConfig) {
        const setVal = (id, val) => {
            const el = document.getElementById(id);
            if (el != null && val != null && val !== '') el.value = val;
        };
        setVal('portal-upload-files-var', existingConfig.uploadFilesVariable);
        setVal('portal-output-var', existingConfig.outputVariable);
        setVal('portal-files-var', existingConfig.filesVariable);
        if (existingConfig.timeout) setVal('portal-timeout', existingConfig.timeout);
        // ownerUserId is a server-stamped passthrough — preserve it verbatim.
        setVal('portal-owner-user-id', existingConfig.ownerUserId);

        const agentFb = document.getElementById('portal-agent-fallback');
        // agentFallback defaults to true; only uncheck when explicitly false.
        if (agentFb) agentFb.checked = existingConfig.agentFallback !== false;

        const continueErr = document.getElementById('portal-continue-on-error');
        if (continueErr) continueErr.checked = existingConfig.continueOnError === true ||
                                               existingConfig.continueOnError === 'true';
    }

    // Populate variable datalist for the variable-name inputs.
    populatePortalVariableDatalist();
}

/**
 * Collect Portal config from the form. Called when saving the node configuration.
 * Mirrors collectIntegrationNodeConfig(): returns a plain object matching the
 * backend `config` key contract.
 */
function collectPortalNodeConfig() {
    const getVal = (id) => {
        const el = document.getElementById(id);
        return el ? (el.value || '') : '';
    };
    const getChecked = (id) => {
        const el = document.getElementById(id);
        return el ? !!el.checked : false;
    };

    let timeout = parseInt(getVal('portal-timeout'), 10);
    if (!Number.isFinite(timeout) || timeout <= 0) timeout = 1200;

    return {
        portalWorkflowSlug: getVal('portal-workflow-selector'),
        uploadFilesVariable: getVal('portal-upload-files-var'),
        outputVariable: getVal('portal-output-var'),
        filesVariable: getVal('portal-files-var'),
        timeout: timeout,
        agentFallback: getChecked('portal-agent-fallback'),
        continueOnError: getChecked('portal-continue-on-error'),
        // Server-stamped passthrough — keep whatever was saved (may be empty
        // for a never-saved node; the backend stamps it at save time).
        ownerUserId: getVal('portal-owner-user-id')
    };
}

// ============================================
// CSS Styles for Portal Node
// ============================================
const portalNodeStyles = document.createElement('style');
portalNodeStyles.textContent = `
    .tool-item[data-type="Portal"] {
        background: linear-gradient(135deg, #0d6efd 0%, #0dcaf0 100%);
        color: white;
        border: none;
    }

    .tool-item[data-type="Portal"]:hover {
        background: linear-gradient(135deg, #0b5ed7 0%, #0aa2c0 100%);
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(13, 110, 253, 0.3);
    }

    .workflow-node[data-type="Portal"] {
        border-color: #0d6efd;
    }

    .workflow-node[data-type="Portal"] .node-header {
        background: linear-gradient(135deg, #0d6efd 0%, #0dcaf0 100%);
        color: white;
    }

    .portal-config .form-label.fw-bold {
        color: #0d6efd;
    }
`;
document.head.appendChild(portalNodeStyles);

// ============================================
// Hook into workflow designer initialization
// ============================================
// When the node config modal is shown, self-initialize if it's a Portal node.
// (Mirrors integration_node.js's shown.bs.modal hook.)
document.addEventListener('shown.bs.modal', function(event) {
    const modal = event.target;
    if (modal.id === 'nodeConfigModal' || modal.classList.contains('node-config-modal')) {
        // Check if this is a Portal node by probing for our selector.
        const portalSelector = modal.querySelector('#portal-workflow-selector');
        if (portalSelector) {
            const existingConfig = (typeof configuredNode !== 'undefined' && configuredNode)
                ? nodeConfigs.get(configuredNode.id) : null;
            initPortalConfigPanel(existingConfig);
        }
    }
});

console.log('Portal node registered successfully');
