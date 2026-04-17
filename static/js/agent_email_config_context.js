/**
 * Page Context: Agent Email Configuration
 * 
 * Provides real-time context about the agent email configuration page.
 * Add this script to agent_email_config.html
 * 
 * Extracts:
 * - Agent info and status
 * - Email address configuration
 * - Inbound settings (receive, auto-respond, workflow, inbox tools)
 * - Safety limits
 * - Configuration state
 */

window.assistantPageContext = {
    page: 'agent_email_config',
    pageName: 'Agent Email Configuration',
    
    getPageData: function() {
        const data = {
            // Agent info
            agent: {
                id: null,
                name: '',
                isActive: false
            },
            
            // Email address settings
            emailAddress: {
                prefix: '',
                displayName: '',
                fullAddress: '',
                isEnabled: false
            },
            
            // Inbound settings
            inbound: {
                receiveEnabled: false,
                autoRespondEnabled: false,
                autoRespondStyle: 'professional',
                autoRespondInstructions: '',
                requireApproval: true,
                workflowTriggerEnabled: false,
                workflowId: '',
                filterRulesCount: 0,
                inboxToolsEnabled: false
            },
            
            // Safety limits
            safety: {
                maxPerDay: 50,
                cooldownMinutes: 15,
                notifyOnReceive: true,
                notifyOnAutoReply: true
            },
            
            // Configuration state
            configState: {
                isConfigured: false,
                isLoading: false,
                hasUnsavedChanges: false
            },
            
            // Modal state
            modal: {
                testEmailOpen: false
            },
            
            // Validation
            validation: {
                isValid: true,
                errors: []
            },
            
            // Available actions
            availableActions: []
        };
        
        // === AGENT INFO ===
        if (typeof agentId !== 'undefined') {
            data.agent.id = agentId;
        }
        
        const agentNameEl = document.getElementById('agentName');
        if (agentNameEl) {
            data.agent.name = agentNameEl.textContent.trim();
        }
        
        const statusBadge = document.getElementById('statusBadge');
        if (statusBadge) {
            data.agent.isActive = statusBadge.classList.contains('status-active');
        }
        
        // === LOADING STATE ===
        const loadingOverlay = document.getElementById('loadingOverlay');
        if (loadingOverlay && loadingOverlay.style.display !== 'none') {
            data.configState.isLoading = true;
        }
        
        // === EMAIL ADDRESS ===
        const prefixEl = document.getElementById('emailPrefix');
        if (prefixEl) {
            data.emailAddress.prefix = prefixEl.value.trim();
        }
        
        const fromNameEl = document.getElementById('fromName');
        if (fromNameEl) {
            data.emailAddress.displayName = fromNameEl.value.trim();
        }
        
        const fullPreviewEl = document.getElementById('fullEmailPreview');
        if (fullPreviewEl) {
            const previewText = fullPreviewEl.textContent.trim();
            if (!previewText.includes('Enter') && !previewText.includes('Configure')) {
                data.emailAddress.fullAddress = previewText;
            }
        }
        
        const emailEnabledEl = document.getElementById('emailEnabled');
        if (emailEnabledEl) {
            data.emailAddress.isEnabled = emailEnabledEl.checked;
        }
        
        // === INBOUND SETTINGS ===
        const inboundEl = document.getElementById('inboundEnabled');
        if (inboundEl) {
            data.inbound.receiveEnabled = inboundEl.checked;
        }
        
        const autoRespondEl = document.getElementById('autoRespondEnabled');
        if (autoRespondEl) {
            data.inbound.autoRespondEnabled = autoRespondEl.checked;
        }
        
        const styleEl = document.getElementById('autoRespondStyle');
        if (styleEl) {
            data.inbound.autoRespondStyle = styleEl.value;
        }
        
        const instructionsEl = document.getElementById('autoRespondInstructions');
        if (instructionsEl) {
            data.inbound.autoRespondInstructions = instructionsEl.value.trim();
        }
        
        const approvalEl = document.getElementById('requireApproval');
        if (approvalEl) {
            data.inbound.requireApproval = approvalEl.checked;
        }
        
        const workflowTriggerEl = document.getElementById('workflowTriggerEnabled');
        if (workflowTriggerEl) {
            data.inbound.workflowTriggerEnabled = workflowTriggerEl.checked;
        }
        
        const workflowIdEl = document.getElementById('workflowId');
        if (workflowIdEl) {
            data.inbound.workflowId = workflowIdEl.value;
        }
        
        // Count filter rules
        const filterRules = document.querySelectorAll('.filter-rule');
        data.inbound.filterRulesCount = filterRules.length;
        
        const inboxToolsEl = document.getElementById('inboxToolsEnabled');
        if (inboxToolsEl) {
            data.inbound.inboxToolsEnabled = inboxToolsEl.checked;
        }
        
        // === SAFETY LIMITS ===
        const maxPerDayEl = document.getElementById('maxPerDay');
        if (maxPerDayEl) {
            data.safety.maxPerDay = parseInt(maxPerDayEl.value) || 50;
        }
        
        const cooldownEl = document.getElementById('cooldownMinutes');
        if (cooldownEl) {
            data.safety.cooldownMinutes = parseInt(cooldownEl.value) || 15;
        }
        
        const notifyReceiveEl = document.getElementById('notifyOnReceive');
        if (notifyReceiveEl) {
            data.safety.notifyOnReceive = notifyReceiveEl.checked;
        }
        
        const notifyReplyEl = document.getElementById('notifyOnAutoReply');
        if (notifyReplyEl) {
            data.safety.notifyOnAutoReply = notifyReplyEl.checked;
        }
        
        // === CONFIG STATE ===
        const deleteBtn = document.getElementById('deleteBtn');
        if (deleteBtn && deleteBtn.style.display !== 'none') {
            data.configState.isConfigured = true;
        }
        
        // Check if configured via global variable
        if (typeof isConfigured !== 'undefined') {
            data.configState.isConfigured = isConfigured;
        }
        
        // === MODAL STATE ===
        const testModal = document.getElementById('testEmailModal');
        if (testModal && testModal.classList.contains('show')) {
            data.modal.testEmailOpen = true;
        }
        
        // === VALIDATION ===
        if (!data.emailAddress.prefix) {
            data.validation.errors.push('Email prefix is required');
        } else if (!/^[a-z0-9-]+$/.test(data.emailAddress.prefix)) {
            data.validation.errors.push('Prefix must be lowercase letters, numbers, and hyphens only');
        }
        
        if (data.inbound.workflowTriggerEnabled && !data.inbound.workflowId) {
            data.validation.errors.push('Workflow must be selected when trigger is enabled');
        }
        
        data.validation.isValid = data.validation.errors.length === 0;
        
        // === AVAILABLE ACTIONS ===
        if (data.configState.isLoading) {
            data.availableActions = [
                'Loading configuration...'
            ];
        } else if (data.modal.testEmailOpen) {
            data.availableActions = [
                'Enter recipient email address',
                'Click Send to send test email',
                'Click Cancel to close'
            ];
        } else if (!data.configState.isConfigured) {
            data.availableActions = [
                'Enter email prefix',
                'Set display name',
                'Configure inbound settings',
                'Save configuration'
            ];
        } else {
            data.availableActions = [
                'Modify email settings',
                'Configure auto-response',
                'Set up workflow triggers',
                'Adjust safety limits',
                'Save changes',
                'Send test email',
                'View inbox'
            ];
            if (data.configState.isConfigured) {
                data.availableActions.push('Delete configuration');
            }
        }
        
        // Debug summary
        console.log('=== Agent Email Config Context ===');
        console.log('Agent:', data.agent.name, '| Active:', data.agent.isActive);
        console.log('Email:', data.emailAddress.fullAddress || '(not configured)');
        console.log('Enabled:', data.emailAddress.isEnabled);
        console.log('Receive:', data.inbound.receiveEnabled,
                   '| Auto-respond:', data.inbound.autoRespondEnabled,
                   '| Workflow:', data.inbound.workflowTriggerEnabled);
        console.log('Configured:', data.configState.isConfigured);
        console.log('Valid:', data.validation.isValid, 
                   data.validation.errors.length ? '| Errors: ' + data.validation.errors.join(', ') : '');
        
        return data;
    }
};

console.log('Agent Email Config context loaded');
