/**
 * Admin UI for Builder Agent Configuration
 * ==========================================
 * Manages the flow-based configuration interface.
 */

// ─── State ─────────────────────────────────────────────────────────────────

let currentSection = 'intent';
let hasUnsavedChanges = false;
let loadedConfigs = {};
let loadedDomains = [];
let loadedActions = [];
let loadedAgents = [];
let currentDomainId = null;
let currentActionId = null;
let currentAgentId = null;
let pendingDeleteCallback = null;

// ─── DOM Elements ──────────────────────────────────────────────────────────

const elements = {
    flowSteps: document.querySelectorAll('.flow-step'),
    sections: document.querySelectorAll('.config-section'),
    sectionTitle: document.getElementById('section-title'),
    sectionDesc: document.getElementById('section-desc'),
    saveStatus: document.getElementById('save-status'),
    btnSaveSection: document.getElementById('btn-save-section'),
    btnSaveAll: document.getElementById('btn-save-all'),
    domainSelect: document.getElementById('domain-select'),
    domainEditor: document.getElementById('domain-editor'),
    actionSelect: document.getElementById('action-select'),
    actionEditor: document.getElementById('action-editor'),
    fuzzyThreshold: document.getElementById('fuzzy-threshold'),
    fuzzyValue: document.getElementById('fuzzy-value'),
    toastContainer: document.getElementById('toast-container'),
};

// Section metadata
const sectionMeta = {
    intent: {
        title: 'Intent Classification',
        desc: 'Configure how user messages are routed to different handlers',
    },
    personality: {
        title: 'System Personality',
        desc: 'Define the agent\'s tone, style, and high-level capabilities',
    },
    knowledge: {
        title: 'Platform Knowledge',
        desc: 'Static knowledge about platform entities, capabilities, and planning rules',
    },
    domains: {
        title: 'Domain Registry',
        desc: 'Define what capabilities exist in each platform domain',
    },
    actions: {
        title: 'Action Registry',
        desc: 'Map capabilities to API routes, methods, and parameters',
    },
    planning: {
        title: 'Planning Prompt',
        desc: 'Configure how the agent creates execution plans',
    },
    extraction: {
        title: 'Step Extraction',
        desc: 'Configure how plans are parsed into structured steps',
    },
    enrichment: {
        title: 'Parameter Enrichment',
        desc: 'Configure how missing parameters are filled in',
    },
    context: {
        title: 'Context Gatherer',
        desc: 'Configure dynamic lookups and validation settings',
    },
    agents: {
        title: 'Agent Registry',
        desc: 'Configure specialized agents for task delegation',
    },
};

// ─── API Functions ─────────────────────────────────────────────────────────

async function fetchConfig(file, variable) {
    try {
        const response = await fetch(`/api/admin/config/${file}/${variable}`);
        if (!response.ok) throw new Error(`Failed to fetch ${file}.${variable}`);
        return await response.json();
    } catch (error) {
        console.error('Error fetching config:', error);
        return null;
    }
}

async function saveConfigs(configs) {
    try {
        const response = await fetch('/api/admin/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ configs }),
        });
        return await response.json();
    } catch (error) {
        console.error('Error saving configs:', error);
        throw error;
    }
}

async function fetchDomains() {
    try {
        const response = await fetch('/api/admin/domains');
        if (!response.ok) throw new Error('Failed to fetch domains');
        const data = await response.json();
        return data.domains;
    } catch (error) {
        console.error('Error fetching domains:', error);
        return [];
    }
}

async function fetchDomain(domainId) {
    try {
        const response = await fetch(`/api/admin/domains/${domainId}`);
        if (!response.ok) throw new Error(`Failed to fetch domain: ${domainId}`);
        return await response.json();
    } catch (error) {
        console.error('Error fetching domain:', error);
        return null;
    }
}

async function fetchActions() {
    try {
        const response = await fetch('/api/admin/actions');
        if (!response.ok) throw new Error('Failed to fetch actions');
        const data = await response.json();
        return data.actions;
    } catch (error) {
        console.error('Error fetching actions:', error);
        return [];
    }
}

async function fetchAction(capabilityId) {
    try {
        const response = await fetch(`/api/admin/actions/${capabilityId}`);
        if (!response.ok) throw new Error(`Failed to fetch action: ${capabilityId}`);
        return await response.json();
    } catch (error) {
        console.error('Error fetching action:', error);
        return null;
    }
}

async function fetchFieldCorrections() {
    try {
        const response = await fetch('/api/admin/field-corrections');
        if (!response.ok) throw new Error('Failed to fetch field corrections');
        const data = await response.json();
        return data.corrections;
    } catch (error) {
        console.error('Error fetching field corrections:', error);
        return [];
    }
}

// ─── CRUD API Functions ─────────────────────────────────────────────────────

async function createDomain(data) {
    const response = await fetch('/api/admin/domains', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to create domain');
    }
    return await response.json();
}

async function updateDomain(domainId, data) {
    const response = await fetch(`/api/admin/domains/${domainId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to update domain');
    }
    return await response.json();
}

async function deleteDomain(domainId) {
    const response = await fetch(`/api/admin/domains/${domainId}`, {
        method: 'DELETE',
    });
    if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to delete domain');
    }
    return await response.json();
}

async function createCapability(domainId, data) {
    const response = await fetch(`/api/admin/domains/${domainId}/capabilities`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to create capability');
    }
    return await response.json();
}

async function deleteCapability(domainId, capabilityId) {
    const response = await fetch(`/api/admin/domains/${domainId}/capabilities/${capabilityId}`, {
        method: 'DELETE',
    });
    if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to delete capability');
    }
    return await response.json();
}

async function updateCapability(domainId, capabilityId, data) {
    const response = await fetch(`/api/admin/domains/${domainId}/capabilities/${capabilityId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to update capability');
    }
    return await response.json();
}

async function createAction(data) {
    const response = await fetch('/api/admin/actions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to create action');
    }
    return await response.json();
}

async function updateAction(capabilityId, data) {
    const response = await fetch(`/api/admin/actions/${capabilityId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to update action');
    }
    return await response.json();
}

async function deleteAction(capabilityId) {
    const response = await fetch(`/api/admin/actions/${capabilityId}`, {
        method: 'DELETE',
    });
    if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to delete action');
    }
    return await response.json();
}

async function createInputField(capabilityId, data) {
    const response = await fetch(`/api/admin/actions/${capabilityId}/fields`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to create field');
    }
    return await response.json();
}

async function deleteInputField(capabilityId, fieldName) {
    const response = await fetch(`/api/admin/actions/${capabilityId}/fields/${fieldName}`, {
        method: 'DELETE',
    });
    if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to delete field');
    }
    return await response.json();
}

async function updateInputField(capabilityId, fieldName, data) {
    const response = await fetch(`/api/admin/actions/${capabilityId}/fields/${fieldName}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to update field');
    }
    return await response.json();
}

async function createResponseMapping(capabilityId, data) {
    const response = await fetch(`/api/admin/actions/${capabilityId}/mappings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to create mapping');
    }
    return await response.json();
}

async function deleteResponseMapping(capabilityId, outputName) {
    const response = await fetch(`/api/admin/actions/${capabilityId}/mappings/${outputName}`, {
        method: 'DELETE',
    });
    if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to delete mapping');
    }
    return await response.json();
}

async function updateResponseMapping(capabilityId, outputName, data) {
    const response = await fetch(`/api/admin/actions/${capabilityId}/mappings/${outputName}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to update mapping');
    }
    return await response.json();
}

// ─── Agent Registry API Functions ───────────────────────────────────────────

async function fetchAgents() {
    try {
        const response = await fetch('/api/admin/agents');
        if (!response.ok) throw new Error('Failed to fetch agents');
        const data = await response.json();
        return data.agents;
    } catch (error) {
        console.error('Error fetching agents:', error);
        return [];
    }
}

async function fetchAgent(agentId) {
    try {
        const response = await fetch(`/api/admin/agents/${agentId}`);
        if (!response.ok) throw new Error(`Failed to fetch agent: ${agentId}`);
        return await response.json();
    } catch (error) {
        console.error('Error fetching agent:', error);
        return null;
    }
}

async function createAgent(data) {
    const response = await fetch('/api/admin/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to create agent');
    }
    return await response.json();
}

async function updateAgent(agentId, data) {
    const response = await fetch(`/api/admin/agents/${agentId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to update agent');
    }
    return await response.json();
}

async function deleteAgent(agentId) {
    const response = await fetch(`/api/admin/agents/${agentId}`, {
        method: 'DELETE',
    });
    if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to delete agent');
    }
    return await response.json();
}

async function testAgentConnection(agentId) {
    const response = await fetch(`/api/admin/agents/${agentId}/test`, {
        method: 'POST',
    });
    if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to test agent');
    }
    return await response.json();
}

// ─── UI Functions ──────────────────────────────────────────────────────────

function showSection(sectionId) {
    // Update flow step active state
    elements.flowSteps.forEach(step => {
        step.classList.toggle('active', step.dataset.section === sectionId);
    });

    // Show/hide sections
    elements.sections.forEach(section => {
        section.classList.toggle('hidden', section.id !== `section-${sectionId}`);
    });

    // Update header
    const meta = sectionMeta[sectionId];
    if (meta) {
        elements.sectionTitle.textContent = meta.title;
        elements.sectionDesc.textContent = meta.desc;
    }

    currentSection = sectionId;

    // Load section data
    loadSectionData(sectionId);
}

async function loadSectionData(sectionId) {
    switch (sectionId) {
        case 'intent':
            await loadConfigTextarea('intent-prompt', 'builder_config', 'INTENT_CLASSIFICATION_PROMPT');
            break;

        case 'personality':
            await loadConfigTextarea('system-prompt', 'builder_config', 'BUILDER_SYSTEM_PROMPT');
            break;

        case 'knowledge':
            await loadConfigTextarea('platform-overview', 'platform_knowledge', 'PLATFORM_OVERVIEW');
            await loadConfigTextarea('context-guidance', 'platform_knowledge', 'DYNAMIC_CONTEXT_GUIDANCE');
            await loadConfigTextarea('valid-capabilities', 'platform_knowledge', 'VALID_CAPABILITIES');
            await loadConfigTextarea('planning-rules', 'platform_knowledge', 'PLANNING_RULES');
            break;

        case 'domains':
            await loadDomainsList();
            break;

        case 'actions':
            await loadActionsList();
            break;

        case 'planning':
            await loadConfigTextarea('plan-prompt', 'nodes', 'PLAN_SYSTEM_PROMPT');
            break;

        case 'extraction':
            await loadConfigTextarea('extract-prompt', 'nodes', 'EXTRACT_STEPS_PROMPT');
            break;

        case 'enrichment':
            await loadConfigTextarea('params-prompt', 'nodes', 'EXTRACT_PARAMS_PROMPT');
            await loadFieldCorrections();
            break;

        case 'context':
            // Context settings are loaded from state/defaults
            break;

        case 'agents':
            await loadAgentsList();
            break;
    }
}

async function loadConfigTextarea(elementId, file, variable) {
    const textarea = document.getElementById(elementId);
    if (!textarea) return;

    const cacheKey = `${file}.${variable}`;

    if (loadedConfigs[cacheKey]) {
        textarea.value = loadedConfigs[cacheKey];
        return;
    }

    textarea.disabled = true;
    textarea.placeholder = 'Loading...';

    const data = await fetchConfig(file, variable);

    if (data) {
        textarea.value = data.value;
        loadedConfigs[cacheKey] = data.value;
    } else {
        textarea.placeholder = 'Failed to load';
    }

    textarea.disabled = false;
}

async function loadDomainsList() {
    if (loadedDomains.length === 0) {
        loadedDomains = await fetchDomains();
    }

    elements.domainSelect.innerHTML = '<option value="">-- Select a domain --</option>';

    for (const domain of loadedDomains) {
        const option = document.createElement('option');
        option.value = domain.id;
        const enabledStatus = domain.enabled === false ? ' (disabled)' : '';
        option.textContent = `${domain.name} (${domain.capability_count} capabilities)${enabledStatus}`;
        elements.domainSelect.appendChild(option);
    }
}

async function loadActionsList() {
    if (loadedActions.length === 0) {
        loadedActions = await fetchActions();
    }

    elements.actionSelect.innerHTML = '<option value="">-- Select an action --</option>';

    // Group by domain
    const byDomain = {};
    for (const action of loadedActions) {
        if (!byDomain[action.domain_id]) {
            byDomain[action.domain_id] = [];
        }
        byDomain[action.domain_id].push(action);
    }

    for (const [domain, actions] of Object.entries(byDomain)) {
        const optgroup = document.createElement('optgroup');
        optgroup.label = domain.toUpperCase();

        for (const action of actions) {
            const option = document.createElement('option');
            option.value = action.capability_id;
            option.textContent = action.capability_id;
            optgroup.appendChild(option);
        }

        elements.actionSelect.appendChild(optgroup);
    }
}

async function loadDomainDetails(domainId) {
    const domain = await fetchDomain(domainId);
    if (!domain) return;

    currentDomainId = domainId;

    document.getElementById('domain-id').value = domain.id;
    document.getElementById('domain-name').value = domain.name;
    document.getElementById('domain-description').value = domain.description;
    document.getElementById('domain-context').value = domain.context_notes || '';
    document.getElementById('domain-enabled').checked = domain.enabled !== false;

    // Render capabilities
    const capsList = document.getElementById('capabilities-list');
    capsList.innerHTML = '';

    for (const cap of domain.capabilities) {
        const capEl = document.createElement('div');
        capEl.className = 'capability-item';
        capEl.dataset.capId = cap.id;
        capEl.dataset.capName = cap.name;
        capEl.dataset.capCategory = cap.category;
        capEl.dataset.capDescription = cap.description;
        capEl.dataset.capTags = cap.tags.join(',');
        capEl.innerHTML = `
            <div class="item-actions">
                <button class="btn-edit" title="Edit capability" data-cap-id="${cap.id}">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/>
                    </svg>
                </button>
                <button class="btn-delete" title="Delete capability" data-cap-id="${cap.id}">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
                    </svg>
                </button>
            </div>
            <div class="capability-header">
                <code class="capability-id">${cap.id}</code>
                <span class="capability-category">${cap.category}</span>
            </div>
            <div class="capability-name">${cap.name}</div>
            <div class="capability-desc">${cap.description}</div>
            ${cap.tags.length > 0 ? `<div class="capability-tags">${cap.tags.map(t => `<span class="tag">${t}</span>`).join('')}</div>` : ''}
        `;
        capsList.appendChild(capEl);
    }

    // Add edit handlers for capabilities
    capsList.querySelectorAll('.btn-edit').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const capEl = btn.closest('.capability-item');
            const capId = capEl.dataset.capId;
            const capName = capEl.dataset.capName;
            const capCategory = capEl.dataset.capCategory;
            const capDescription = capEl.dataset.capDescription;
            const capTags = capEl.dataset.capTags;

            // Populate edit modal
            document.getElementById('edit-cap-original-id').value = capId;
            document.getElementById('edit-cap-id').value = capId;
            document.getElementById('edit-cap-name').value = capName;
            document.getElementById('edit-cap-category').value = capCategory;
            document.getElementById('edit-cap-description').value = capDescription;
            document.getElementById('edit-cap-tags').value = capTags;
            showModal('modal-edit-capability');
        });
    });

    // Add delete handlers for capabilities
    capsList.querySelectorAll('.btn-delete').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const capId = btn.dataset.capId;
            showDeleteConfirm(`Delete capability "${capId}"?`, async () => {
                try {
                    await deleteCapability(currentDomainId, capId);
                    showToast('Capability deleted', 'success');
                    loadedDomains = []; // Clear cache
                    await loadDomainDetails(currentDomainId);
                } catch (error) {
                    showToast(error.message, 'error');
                }
            });
        });
    });

    elements.domainEditor.classList.remove('hidden');
}

async function loadActionDetails(capabilityId) {
    const action = await fetchAction(capabilityId);
    if (!action) return;

    currentActionId = capabilityId;

    document.getElementById('action-capability-id').value = action.capability_id;
    document.getElementById('action-domain').value = action.domain_id;
    document.getElementById('action-description').value = action.description;

    if (action.route) {
        document.getElementById('route-method').value = action.route.method;
        document.getElementById('route-path').value = action.route.path;
    }

    // Render input fields
    const fieldsList = document.getElementById('input-fields-list');
    fieldsList.innerHTML = '';

    for (const field of action.input_fields) {
        const fieldEl = document.createElement('div');
        fieldEl.className = 'field-item';
        fieldEl.dataset.fieldName = field.name;
        fieldEl.dataset.fieldType = field.type;
        fieldEl.dataset.fieldRequired = field.required;
        fieldEl.dataset.fieldDescription = field.description || '';
        fieldEl.dataset.fieldDefault = field.default !== null && field.default !== undefined ? JSON.stringify(field.default) : '';
        fieldEl.innerHTML = `
            <div class="item-actions">
                <button class="btn-edit" title="Edit field" data-field-name="${field.name}">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/>
                    </svg>
                </button>
                <button class="btn-delete" title="Delete field" data-field-name="${field.name}">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
                    </svg>
                </button>
            </div>
            <div class="field-header">
                <code class="field-name">${field.name}</code>
                <span class="field-type">${field.type}</span>
                ${field.required ? '<span class="field-required">required</span>' : '<span class="field-optional">optional</span>'}
            </div>
            <div class="field-desc">${field.description || ''}</div>
            ${field.default !== null && field.default !== undefined ? `<div class="field-default">Default: <code>${JSON.stringify(field.default)}</code></div>` : ''}
        `;
        fieldsList.appendChild(fieldEl);
    }

    // Add edit handlers for fields
    fieldsList.querySelectorAll('.btn-edit').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const fieldEl = btn.closest('.field-item');
            const fieldName = fieldEl.dataset.fieldName;
            const fieldType = fieldEl.dataset.fieldType;
            const fieldRequired = fieldEl.dataset.fieldRequired === 'true';
            const fieldDescription = fieldEl.dataset.fieldDescription;
            const fieldDefault = fieldEl.dataset.fieldDefault;

            // Populate edit modal
            document.getElementById('edit-field-original-name').value = fieldName;
            document.getElementById('edit-field-name').value = fieldName;
            document.getElementById('edit-field-type').value = fieldType.toLowerCase();
            document.getElementById('edit-field-required').value = fieldRequired ? 'true' : 'false';
            document.getElementById('edit-field-description').value = fieldDescription;
            document.getElementById('edit-field-default').value = fieldDefault;
            showModal('modal-edit-field');
        });
    });

    // Add delete handlers for fields
    fieldsList.querySelectorAll('.btn-delete').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const fieldName = btn.dataset.fieldName;
            showDeleteConfirm(`Delete field "${fieldName}"?`, async () => {
                try {
                    await deleteInputField(currentActionId, fieldName);
                    showToast('Field deleted', 'success');
                    loadedActions = []; // Clear cache
                    await loadActionDetails(currentActionId);
                } catch (error) {
                    showToast(error.message, 'error');
                }
            });
        });
    });

    // Render response mappings
    const mappingsList = document.getElementById('response-mappings-list');
    mappingsList.innerHTML = '';

    if (action.response_mappings.length === 0) {
        mappingsList.innerHTML = '<div class="text-zinc-500 text-sm">No response mappings defined</div>';
    } else {
        for (const mapping of action.response_mappings) {
            const mapEl = document.createElement('div');
            mapEl.className = 'mapping-item';
            mapEl.dataset.outputName = mapping.output_name;
            mapEl.dataset.sourcePath = mapping.source_path;
            mapEl.dataset.mappingDescription = mapping.description || '';
            mapEl.innerHTML = `
                <div class="item-actions">
                    <button class="btn-edit" title="Edit mapping" data-output-name="${mapping.output_name}">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/>
                        </svg>
                    </button>
                    <button class="btn-delete" title="Delete mapping" data-output-name="${mapping.output_name}">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
                        </svg>
                    </button>
                </div>
                <div class="mapping-header">
                    <code class="mapping-output">${mapping.output_name}</code>
                    <span class="mapping-arrow">←</span>
                    <code class="mapping-source">${mapping.source_path}</code>
                </div>
                <div class="mapping-desc">${mapping.description || ''}</div>
            `;
            mappingsList.appendChild(mapEl);
        }

        // Add edit handlers for mappings
        mappingsList.querySelectorAll('.btn-edit').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const mapEl = btn.closest('.mapping-item');
                const outputName = mapEl.dataset.outputName;
                const sourcePath = mapEl.dataset.sourcePath;
                const mappingDescription = mapEl.dataset.mappingDescription;

                // Populate edit modal
                document.getElementById('edit-mapping-original-output').value = outputName;
                document.getElementById('edit-mapping-output').value = outputName;
                document.getElementById('edit-mapping-source').value = sourcePath;
                document.getElementById('edit-mapping-description').value = mappingDescription;
                showModal('modal-edit-mapping');
            });
        });

        // Add delete handlers for mappings
        mappingsList.querySelectorAll('.btn-delete').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const outputName = btn.dataset.outputName;
                showDeleteConfirm(`Delete mapping "${outputName}"?`, async () => {
                    try {
                        await deleteResponseMapping(currentActionId, outputName);
                        showToast('Mapping deleted', 'success');
                        loadedActions = []; // Clear cache
                        await loadActionDetails(currentActionId);
                    } catch (error) {
                        showToast(error.message, 'error');
                    }
                });
            });
        });
    }

    elements.actionEditor.classList.remove('hidden');
}

async function loadFieldCorrections() {
    const corrections = await fetchFieldCorrections();
    const container = document.getElementById('field-corrections');
    container.innerHTML = '';

    for (const correction of corrections) {
        const corrEl = document.createElement('div');
        corrEl.className = 'correction-item';
        corrEl.innerHTML = `
            <input type="text" class="correction-wrong" value="${correction.wrong}" placeholder="Wrong name">
            <span class="correction-arrow">→</span>
            <input type="text" class="correction-correct" value="${correction.correct}" placeholder="Correct name">
            <button class="correction-delete" title="Remove">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
                </svg>
            </button>
        `;
        container.appendChild(corrEl);
    }
}

async function loadAgentsList() {
    const agentSelect = document.getElementById('agent-select');
    if (!agentSelect) return;

    if (loadedAgents.length === 0) {
        loadedAgents = await fetchAgents();
    }

    agentSelect.innerHTML = '<option value="">-- Select an agent --</option>';

    for (const agent of loadedAgents) {
        const option = document.createElement('option');
        option.value = agent.id;
        option.textContent = `${agent.name} ${agent.enabled ? '' : '(disabled)'}`;
        agentSelect.appendChild(option);
    }
}

async function loadAgentDetails(agentId) {
    const agent = await fetchAgent(agentId);
    if (!agent) return;

    currentAgentId = agentId;

    document.getElementById('agent-id').value = agent.id;
    document.getElementById('agent-name').value = agent.name;
    document.getElementById('agent-description').value = agent.description || '';
    document.getElementById('agent-protocol').value = agent.protocol || 'text_chat';
    document.getElementById('agent-timeout').value = agent.timeout || 120;
    document.getElementById('agent-endpoint').value = agent.endpoint || '';
    document.getElementById('agent-specializations').value = (agent.specializations || []).join(', ');
    document.getElementById('agent-system-prompt').value = agent.system_prompt || '';
    document.getElementById('agent-enabled').checked = agent.enabled !== false;

    document.getElementById('agent-editor').classList.remove('hidden');
}

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;

    elements.toastContainer.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('toast-fade');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

async function saveCurrentSection() {
    const section = document.getElementById(`section-${currentSection}`);
    if (!section) return;

    const textareas = section.querySelectorAll('textarea[data-file][data-var]');
    const configs = [];

    for (const textarea of textareas) {
        configs.push({
            file: textarea.dataset.file,
            variable: textarea.dataset.var,
            value: textarea.value,
        });
    }

    if (configs.length === 0) {
        showToast('Nothing to save in this section', 'info');
        return;
    }

    elements.btnSaveSection.disabled = true;
    elements.saveStatus.textContent = 'Saving...';

    try {
        const result = await saveConfigs(configs);

        const successCount = result.results.filter(r => r.status === 'success').length;
        const failCount = result.results.filter(r => r.status === 'error').length;

        if (failCount === 0) {
            showToast(`Saved ${successCount} config(s) successfully`, 'success');
            elements.saveStatus.textContent = 'Saved!';

            // Update cache
            for (const config of configs) {
                loadedConfigs[`${config.file}.${config.variable}`] = config.value;
            }
        } else {
            showToast(`${failCount} config(s) failed to save`, 'error');
            elements.saveStatus.textContent = 'Some errors';
        }
    } catch (error) {
        showToast('Failed to save: ' + error.message, 'error');
        elements.saveStatus.textContent = 'Error';
    }

    elements.btnSaveSection.disabled = false;
    setTimeout(() => elements.saveStatus.textContent = '', 3000);
}

async function saveAllChanges() {
    // Collect all textareas with unsaved changes
    const allTextareas = document.querySelectorAll('textarea[data-file][data-var]');
    const configs = [];

    for (const textarea of allTextareas) {
        const cacheKey = `${textarea.dataset.file}.${textarea.dataset.var}`;
        if (loadedConfigs[cacheKey] !== textarea.value) {
            configs.push({
                file: textarea.dataset.file,
                variable: textarea.dataset.var,
                value: textarea.value,
            });
        }
    }

    if (configs.length === 0) {
        showToast('No changes to save', 'info');
        return;
    }

    elements.btnSaveAll.disabled = true;

    try {
        const result = await saveConfigs(configs);

        const successCount = result.results.filter(r => r.status === 'success').length;
        showToast(`Saved ${successCount} config(s) successfully`, 'success');

        // Update cache
        for (const config of configs) {
            loadedConfigs[`${config.file}.${config.variable}`] = config.value;
        }

        hasUnsavedChanges = false;
    } catch (error) {
        showToast('Failed to save: ' + error.message, 'error');
    }

    elements.btnSaveAll.disabled = false;
}

// ─── Event Listeners ───────────────────────────────────────────────────────

// Flow step navigation
elements.flowSteps.forEach(step => {
    step.addEventListener('click', () => {
        showSection(step.dataset.section);
    });
});

// Domain selector
elements.domainSelect?.addEventListener('change', (e) => {
    if (e.target.value) {
        loadDomainDetails(e.target.value);
    } else {
        elements.domainEditor.classList.add('hidden');
    }
});

// Action selector
elements.actionSelect?.addEventListener('change', (e) => {
    if (e.target.value) {
        loadActionDetails(e.target.value);
    } else {
        elements.actionEditor.classList.add('hidden');
    }
});

// Fuzzy threshold slider
elements.fuzzyThreshold?.addEventListener('input', (e) => {
    elements.fuzzyValue.textContent = parseFloat(e.target.value).toFixed(2);
});

// Save buttons
elements.btnSaveSection?.addEventListener('click', saveCurrentSection);
elements.btnSaveAll?.addEventListener('click', saveAllChanges);

// Track changes
document.addEventListener('input', (e) => {
    if (e.target.matches('textarea[data-file][data-var]')) {
        hasUnsavedChanges = true;
    }
});

// Warn before leaving with unsaved changes
window.addEventListener('beforeunload', (e) => {
    if (hasUnsavedChanges) {
        e.preventDefault();
        e.returnValue = '';
    }
});

// ─── Modal Functions ────────────────────────────────────────────────────────

function showModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.remove('hidden');
    }
}

function hideModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.add('hidden');
    }
}

function showDeleteConfirm(message, callback) {
    document.getElementById('delete-confirm-message').textContent = message;
    pendingDeleteCallback = callback;
    showModal('modal-confirm-delete');
}

// Setup modal close handlers
document.querySelectorAll('.modal').forEach(modal => {
    const backdrop = modal.querySelector('.modal-backdrop');
    const closeBtn = modal.querySelector('.modal-close');
    const cancelBtn = modal.querySelector('.modal-btn-cancel');

    if (backdrop) {
        backdrop.addEventListener('click', () => modal.classList.add('hidden'));
    }
    if (closeBtn) {
        closeBtn.addEventListener('click', () => modal.classList.add('hidden'));
    }
    if (cancelBtn) {
        cancelBtn.addEventListener('click', () => modal.classList.add('hidden'));
    }
});

// Delete confirmation handler
document.getElementById('btn-confirm-delete')?.addEventListener('click', async () => {
    if (pendingDeleteCallback) {
        await pendingDeleteCallback();
        pendingDeleteCallback = null;
    }
    hideModal('modal-confirm-delete');
});

// ─── Domain CRUD Handlers ───────────────────────────────────────────────────

document.getElementById('btn-add-domain')?.addEventListener('click', () => {
    // Clear form
    document.getElementById('new-domain-id').value = '';
    document.getElementById('new-domain-name').value = '';
    document.getElementById('new-domain-description').value = '';
    document.getElementById('new-domain-context').value = '';
    document.getElementById('new-domain-enabled').checked = true;
    showModal('modal-add-domain');
});

document.getElementById('btn-confirm-add-domain')?.addEventListener('click', async () => {
    const id = document.getElementById('new-domain-id').value.trim();
    const name = document.getElementById('new-domain-name').value.trim();
    const description = document.getElementById('new-domain-description').value.trim();
    const context_notes = document.getElementById('new-domain-context').value.trim();
    const enabled = document.getElementById('new-domain-enabled').checked;

    if (!id || !name) {
        showToast('Domain ID and Name are required', 'error');
        return;
    }

    try {
        await createDomain({ id, name, description, context_notes, enabled });
        showToast('Domain created successfully', 'success');
        hideModal('modal-add-domain');
        loadedDomains = []; // Clear cache
        await loadDomainsList();
        elements.domainSelect.value = id;
        await loadDomainDetails(id);
    } catch (error) {
        showToast(error.message, 'error');
    }
});

document.getElementById('btn-delete-domain')?.addEventListener('click', () => {
    if (!currentDomainId) return;
    showDeleteConfirm(`Delete domain "${currentDomainId}" and all its capabilities?`, async () => {
        try {
            await deleteDomain(currentDomainId);
            showToast('Domain deleted', 'success');
            loadedDomains = []; // Clear cache
            currentDomainId = null;
            elements.domainEditor.classList.add('hidden');
            elements.domainSelect.value = '';
            await loadDomainsList();
        } catch (error) {
            showToast(error.message, 'error');
        }
    });
});

// Save domain button
document.getElementById('btn-save-domain')?.addEventListener('click', async () => {
    if (!currentDomainId) return;

    const name = document.getElementById('domain-name').value.trim();
    const description = document.getElementById('domain-description').value.trim();
    const context_notes = document.getElementById('domain-context').value.trim();
    const enabled = document.getElementById('domain-enabled').checked;

    if (!name) {
        showToast('Domain name is required', 'error');
        return;
    }

    try {
        await updateDomain(currentDomainId, {
            name,
            description,
            context_notes,
            enabled,
        });
        showToast('Domain updated successfully', 'success');
        loadedDomains = []; // Clear cache
        await loadDomainsList();
    } catch (error) {
        showToast(error.message, 'error');
    }
});

// ─── Capability CRUD Handlers ───────────────────────────────────────────────

document.getElementById('btn-add-capability')?.addEventListener('click', () => {
    if (!currentDomainId) {
        showToast('Select a domain first', 'error');
        return;
    }
    // Clear form
    document.getElementById('new-cap-id').value = '';
    document.getElementById('new-cap-name').value = '';
    document.getElementById('new-cap-category').value = 'create';
    document.getElementById('new-cap-description').value = '';
    document.getElementById('new-cap-tags').value = '';
    showModal('modal-add-capability');
});

document.getElementById('btn-confirm-add-capability')?.addEventListener('click', async () => {
    const id = document.getElementById('new-cap-id').value.trim();
    const name = document.getElementById('new-cap-name').value.trim();
    const category = document.getElementById('new-cap-category').value;
    const description = document.getElementById('new-cap-description').value.trim();
    const tagsStr = document.getElementById('new-cap-tags').value.trim();
    const tags = tagsStr ? tagsStr.split(',').map(t => t.trim()).filter(t => t) : [];

    if (!id || !name) {
        showToast('Capability ID and Name are required', 'error');
        return;
    }

    try {
        await createCapability(currentDomainId, { id, name, category, description, tags });
        showToast('Capability created successfully', 'success');
        hideModal('modal-add-capability');
        loadedDomains = []; // Clear cache
        await loadDomainDetails(currentDomainId);
    } catch (error) {
        showToast(error.message, 'error');
    }
});

// Edit capability handler
document.getElementById('btn-confirm-edit-capability')?.addEventListener('click', async () => {
    const originalId = document.getElementById('edit-cap-original-id').value;
    const id = document.getElementById('edit-cap-id').value.trim();
    const name = document.getElementById('edit-cap-name').value.trim();
    const category = document.getElementById('edit-cap-category').value;
    const description = document.getElementById('edit-cap-description').value.trim();
    const tagsStr = document.getElementById('edit-cap-tags').value.trim();
    const tags = tagsStr ? tagsStr.split(',').map(t => t.trim()).filter(t => t) : [];

    if (!name) {
        showToast('Capability name is required', 'error');
        return;
    }

    try {
        await updateCapability(currentDomainId, originalId, { id, name, category, description, tags });
        showToast('Capability updated successfully', 'success');
        hideModal('modal-edit-capability');
        loadedDomains = []; // Clear cache
        await loadDomainDetails(currentDomainId);
    } catch (error) {
        showToast(error.message, 'error');
    }
});

// ─── Action CRUD Handlers ───────────────────────────────────────────────────

document.getElementById('btn-add-action')?.addEventListener('click', async () => {
    // Populate domain select
    const domainSelect = document.getElementById('new-action-domain');
    domainSelect.innerHTML = '<option value="">-- Select domain --</option>';

    if (loadedDomains.length === 0) {
        loadedDomains = await fetchDomains();
    }

    for (const domain of loadedDomains) {
        const option = document.createElement('option');
        option.value = domain.id;
        option.textContent = domain.name;
        domainSelect.appendChild(option);
    }

    // Clear form
    document.getElementById('new-action-capability').value = '';
    document.getElementById('new-action-description').value = '';
    document.getElementById('new-action-method').value = 'POST';
    document.getElementById('new-action-path').value = '';
    showModal('modal-add-action');
});

document.getElementById('btn-confirm-add-action')?.addEventListener('click', async () => {
    const domain_id = document.getElementById('new-action-domain').value;
    const capability_id = document.getElementById('new-action-capability').value.trim();
    const description = document.getElementById('new-action-description').value.trim();
    const method = document.getElementById('new-action-method').value;
    const path = document.getElementById('new-action-path').value.trim();

    if (!domain_id || !capability_id || !path) {
        showToast('Domain, Capability ID, and Path are required', 'error');
        return;
    }

    // Full capability ID includes domain
    const fullCapabilityId = `${domain_id}.${capability_id}`;

    try {
        await createAction({
            domain_id,
            capability_id: fullCapabilityId,
            description,
            route: { method, path }
        });
        showToast('Action created successfully', 'success');
        hideModal('modal-add-action');
        loadedActions = []; // Clear cache
        await loadActionsList();
        elements.actionSelect.value = fullCapabilityId;
        await loadActionDetails(fullCapabilityId);
    } catch (error) {
        showToast(error.message, 'error');
    }
});

document.getElementById('btn-delete-action')?.addEventListener('click', () => {
    if (!currentActionId) return;
    showDeleteConfirm(`Delete action "${currentActionId}" and all its fields?`, async () => {
        try {
            await deleteAction(currentActionId);
            showToast('Action deleted', 'success');
            loadedActions = []; // Clear cache
            currentActionId = null;
            elements.actionEditor.classList.add('hidden');
            elements.actionSelect.value = '';
            await loadActionsList();
        } catch (error) {
            showToast(error.message, 'error');
        }
    });
});

// Save action button
const btnSaveAction = document.getElementById('btn-save-action');
if (btnSaveAction) {
    btnSaveAction.addEventListener('click', async () => {
        if (!currentActionId) {
            showToast('No action selected', 'error');
            return;
        }

        const description = document.getElementById('action-description').value.trim();
        const method = document.getElementById('route-method').value;
        const path = document.getElementById('route-path').value.trim();
        const domainId = document.getElementById('action-domain').value.trim();
        const capabilityId = document.getElementById('action-capability-id').value.trim();

        if (!path) {
            showToast('Route path is required', 'error');
            return;
        }

        // Collect input fields from the rendered list
        const inputFields = [];
        document.querySelectorAll('#input-fields-list .field-item').forEach(el => {
            const defaultVal = el.dataset.fieldDefault;
            let parsedDefault = null;
            if (defaultVal) {
                try { parsedDefault = JSON.parse(defaultVal); } catch(e) { parsedDefault = defaultVal; }
            }
            inputFields.push({
                name: el.dataset.fieldName,
                type: el.dataset.fieldType,
                required: el.dataset.fieldRequired === 'true',
                description: el.dataset.fieldDescription || '',
                default: parsedDefault,
            });
        });

        // Collect response mappings from the rendered list
        const responseMappings = [];
        document.querySelectorAll('#response-mappings-list .mapping-item').forEach(el => {
            responseMappings.push({
                output_name: el.dataset.outputName,
                source_path: el.dataset.sourcePath,
                description: el.dataset.mappingDescription || '',
            });
        });

        try {
            await updateAction(currentActionId, {
                capability_id: capabilityId,
                domain_id: domainId,
                description,
                notes: '',
                route: { method, path },
                input_fields: inputFields,
                response_mappings: responseMappings,
            });
            showToast('Action updated successfully', 'success');
            loadedActions = []; // Clear cache
            await loadActionDetails(currentActionId);
        } catch (error) {
            showToast(error.message || 'Failed to update action', 'error');
        }
    });
}

// ─── Input Field CRUD Handlers ──────────────────────────────────────────────

document.getElementById('btn-add-input-field')?.addEventListener('click', () => {
    if (!currentActionId) {
        showToast('Select an action first', 'error');
        return;
    }
    // Clear form
    document.getElementById('new-field-name').value = '';
    document.getElementById('new-field-type').value = 'string';
    document.getElementById('new-field-required').value = 'true';
    document.getElementById('new-field-description').value = '';
    document.getElementById('new-field-default').value = '';
    showModal('modal-add-field');
});

document.getElementById('btn-confirm-add-field')?.addEventListener('click', async () => {
    const name = document.getElementById('new-field-name').value.trim();
    const type = document.getElementById('new-field-type').value;
    const required = document.getElementById('new-field-required').value === 'true';
    const description = document.getElementById('new-field-description').value.trim();
    const defaultStr = document.getElementById('new-field-default').value.trim();

    if (!name) {
        showToast('Field name is required', 'error');
        return;
    }

    let default_value = null;
    if (defaultStr) {
        try {
            default_value = JSON.parse(defaultStr);
        } catch (e) {
            showToast('Invalid JSON for default value', 'error');
            return;
        }
    }

    try {
        await createInputField(currentActionId, { name, type, required, description, default: default_value });
        showToast('Field created successfully', 'success');
        hideModal('modal-add-field');
        loadedActions = []; // Clear cache
        await loadActionDetails(currentActionId);
    } catch (error) {
        showToast(error.message, 'error');
    }
});

// Edit input field handler
document.getElementById('btn-confirm-edit-field')?.addEventListener('click', async () => {
    const originalName = document.getElementById('edit-field-original-name').value;
    const name = document.getElementById('edit-field-name').value.trim();
    const type = document.getElementById('edit-field-type').value;
    const required = document.getElementById('edit-field-required').value === 'true';
    const description = document.getElementById('edit-field-description').value.trim();
    const defaultStr = document.getElementById('edit-field-default').value.trim();

    let default_value = null;
    if (defaultStr) {
        try {
            default_value = JSON.parse(defaultStr);
        } catch (e) {
            showToast('Invalid JSON for default value', 'error');
            return;
        }
    }

    try {
        await updateInputField(currentActionId, originalName, { name, type, required, description, default: default_value });
        showToast('Field updated successfully', 'success');
        hideModal('modal-edit-field');
        loadedActions = []; // Clear cache
        await loadActionDetails(currentActionId);
    } catch (error) {
        showToast(error.message, 'error');
    }
});

// ─── Response Mapping CRUD Handlers ─────────────────────────────────────────

document.getElementById('btn-add-response-mapping')?.addEventListener('click', () => {
    if (!currentActionId) {
        showToast('Select an action first', 'error');
        return;
    }
    // Clear form
    document.getElementById('new-mapping-output').value = '';
    document.getElementById('new-mapping-source').value = '';
    document.getElementById('new-mapping-description').value = '';
    showModal('modal-add-mapping');
});

document.getElementById('btn-confirm-add-mapping')?.addEventListener('click', async () => {
    const output_name = document.getElementById('new-mapping-output').value.trim();
    const source_path = document.getElementById('new-mapping-source').value.trim();
    const description = document.getElementById('new-mapping-description').value.trim();

    if (!output_name || !source_path) {
        showToast('Output name and source path are required', 'error');
        return;
    }

    try {
        await createResponseMapping(currentActionId, { output_name, source_path, description });
        showToast('Mapping created successfully', 'success');
        hideModal('modal-add-mapping');
        loadedActions = []; // Clear cache
        await loadActionDetails(currentActionId);
    } catch (error) {
        showToast(error.message, 'error');
    }
});

// Edit response mapping handler
document.getElementById('btn-confirm-edit-mapping')?.addEventListener('click', async () => {
    const originalOutput = document.getElementById('edit-mapping-original-output').value;
    const output_name = document.getElementById('edit-mapping-output').value.trim();
    const source_path = document.getElementById('edit-mapping-source').value.trim();
    const description = document.getElementById('edit-mapping-description').value.trim();

    if (!source_path) {
        showToast('Source path is required', 'error');
        return;
    }

    try {
        await updateResponseMapping(currentActionId, originalOutput, { output_name, source_path, description });
        showToast('Mapping updated successfully', 'success');
        hideModal('modal-edit-mapping');
        loadedActions = []; // Clear cache
        await loadActionDetails(currentActionId);
    } catch (error) {
        showToast(error.message, 'error');
    }
});

// ─── Agent Registry Handlers ─────────────────────────────────────────────────

// Agent selector
document.getElementById('agent-select')?.addEventListener('change', (e) => {
    if (e.target.value) {
        loadAgentDetails(e.target.value);
    } else {
        document.getElementById('agent-editor')?.classList.add('hidden');
    }
});

// Add agent button
document.getElementById('btn-add-agent')?.addEventListener('click', () => {
    // Clear form
    document.getElementById('new-agent-id').value = '';
    document.getElementById('new-agent-name').value = '';
    document.getElementById('new-agent-description').value = '';
    document.getElementById('new-agent-protocol').value = 'text_chat';
    document.getElementById('new-agent-timeout').value = '120';
    document.getElementById('new-agent-endpoint').value = '';
    document.getElementById('new-agent-specializations').value = '';
    document.getElementById('new-agent-system-prompt').value = '';
    showModal('modal-add-agent');
});

// Confirm add agent
document.getElementById('btn-confirm-add-agent')?.addEventListener('click', async () => {
    const id = document.getElementById('new-agent-id').value.trim();
    const name = document.getElementById('new-agent-name').value.trim();
    const description = document.getElementById('new-agent-description').value.trim();
    const protocol = document.getElementById('new-agent-protocol').value;
    const timeout = parseInt(document.getElementById('new-agent-timeout').value) || 120;
    const endpoint = document.getElementById('new-agent-endpoint').value.trim();
    const specializationsStr = document.getElementById('new-agent-specializations').value.trim();
    const specializations = specializationsStr ? specializationsStr.split(',').map(s => s.trim()).filter(s => s) : [];
    const system_prompt = document.getElementById('new-agent-system-prompt').value.trim();

    if (!id || !name || !endpoint) {
        showToast('Agent ID, Name, and Endpoint are required', 'error');
        return;
    }

    try {
        await createAgent({
            id,
            name,
            description,
            protocol,
            timeout,
            endpoint,
            specializations,
            system_prompt,
            enabled: true,
        });
        showToast('Agent created successfully', 'success');
        hideModal('modal-add-agent');
        loadedAgents = []; // Clear cache
        await loadAgentsList();
        document.getElementById('agent-select').value = id;
        await loadAgentDetails(id);
    } catch (error) {
        showToast(error.message, 'error');
    }
});

// Save agent button
document.getElementById('btn-save-agent')?.addEventListener('click', async () => {
    if (!currentAgentId) return;

    const name = document.getElementById('agent-name').value.trim();
    const description = document.getElementById('agent-description').value.trim();
    const protocol = document.getElementById('agent-protocol').value;
    const timeout = parseInt(document.getElementById('agent-timeout').value) || 120;
    const endpoint = document.getElementById('agent-endpoint').value.trim();
    const specializationsStr = document.getElementById('agent-specializations').value.trim();
    const specializations = specializationsStr ? specializationsStr.split(',').map(s => s.trim()).filter(s => s) : [];
    const system_prompt = document.getElementById('agent-system-prompt').value.trim();
    const enabled = document.getElementById('agent-enabled').checked;

    if (!name || !endpoint) {
        showToast('Agent Name and Endpoint are required', 'error');
        return;
    }

    try {
        await updateAgent(currentAgentId, {
            name,
            description,
            protocol,
            timeout,
            endpoint,
            specializations,
            system_prompt,
            enabled,
        });
        showToast('Agent updated successfully', 'success');
        loadedAgents = []; // Clear cache
        await loadAgentsList();
    } catch (error) {
        showToast(error.message, 'error');
    }
});

// Delete agent button
document.getElementById('btn-delete-agent')?.addEventListener('click', () => {
    if (!currentAgentId) return;
    showDeleteConfirm(`Delete agent "${currentAgentId}"?`, async () => {
        try {
            await deleteAgent(currentAgentId);
            showToast('Agent deleted', 'success');
            loadedAgents = []; // Clear cache
            currentAgentId = null;
            document.getElementById('agent-editor')?.classList.add('hidden');
            document.getElementById('agent-select').value = '';
            await loadAgentsList();
        } catch (error) {
            showToast(error.message, 'error');
        }
    });
});

// Test agent connection button
document.getElementById('btn-test-agent')?.addEventListener('click', async () => {
    if (!currentAgentId) return;

    const btn = document.getElementById('btn-test-agent');
    const originalText = btn.innerHTML;
    btn.innerHTML = '<svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg> Testing...';
    btn.disabled = true;

    try {
        const result = await testAgentConnection(currentAgentId);
        if (result.healthy) {
            showToast('Agent is reachable and healthy', 'success');
        } else {
            showToast('Agent is not responding', 'error');
        }
    } catch (error) {
        showToast(error.message, 'error');
    }

    btn.innerHTML = originalText;
    btn.disabled = false;
});

// ─── Initialize ────────────────────────────────────────────────────────────

// Show initial section
showSection('intent');
