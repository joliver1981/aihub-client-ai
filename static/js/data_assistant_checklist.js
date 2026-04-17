/**
 * Data Assistant Setup Checklist
 * 
 * Manages the multi-step setup flow for Data Assistants:
 * 1. Create database connection
 * 2. Build data dictionary
 * 3. Create data agent
 * 4. Test first query
 * 
 * State is persisted via the onboarding API.
 */

class DataAssistantChecklist {
    constructor() {
        this.steps = ['connection', 'dictionary', 'agent'];
        this.state = {
            active: false,
            completed: [],
            dismissed: false
        };
        this.isCollapsed = false;
        
        this.init();
    }
    
    async init() {
        try {
            await this.loadState();
            
            // Only show if user selected data-agent path and hasn't completed/dismissed
            if (this.state.active && !this.state.dismissed && !this.isAllComplete()) {
                this.show();
                this.render();
                this.highlightCurrentPage();
            }
            
            // Check if all complete
            if (this.isAllComplete() && this.state.active) {
                this.showCompletionToast();
            }
        } catch (error) {
            console.error('DataAssistantChecklist init error:', error);
        }
    }
    
    // =========================================================================
    // State Management
    // =========================================================================
    
    async loadState() {
        try {
            const response = await fetch('/api/onboarding/checklist/data-assistant');
            const data = await response.json();
            
            if (data.success) {
                this.state = {
                    active: data.active || false,
                    completed: data.completed || [],
                    dismissed: data.dismissed || false
                };
            }
        } catch (error) {
            console.error('Error loading checklist state:', error);
        }
    }
    
    async saveState() {
        try {
            await fetch('/api/onboarding/checklist/data-assistant', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(this.state)
            });
        } catch (error) {
            console.error('Error saving checklist state:', error);
        }
    }
    
    async completeStep(stepName) {
        if (!this.state.completed.includes(stepName)) {
            this.state.completed.push(stepName);
            await this.saveState();
            this.render();
            
            // Check if all complete
            if (this.isAllComplete()) {
                setTimeout(() => {
                    this.hide();
                    this.showCompletionToast();
                }, 500);
            }
        }
    }
    
    async activate() {
        this.state.active = true;
        this.state.dismissed = false;
        await this.saveState();
        this.show();
        this.render();
    }
    
    async dismiss() {
        this.state.dismissed = true;
        await this.saveState();
        this.hide();
    }
    
    isStepComplete(stepName) {
        return this.state.completed.includes(stepName);
    }
    
    isAllComplete() {
        return this.steps.every(step => this.state.completed.includes(step));
    }
    
    getNextIncompleteStep() {
        return this.steps.find(step => !this.state.completed.includes(step));
    }
    
    getCompletedCount() {
        return this.state.completed.length;
    }
    
    // =========================================================================
    // UI Rendering
    // =========================================================================
    
    show() {
        $('#dataAssistantChecklist').removeClass('d-none');
    }
    
    hide() {
        $('#dataAssistantChecklist').addClass('d-none');
    }
    
    render() {
        const completedCount = this.getCompletedCount();
        const totalSteps = this.steps.length;
        const progressPercent = (completedCount / totalSteps) * 100;
        
        // Update progress text and bar
        $('#checklistProgressText').text(`${completedCount} of ${totalSteps}`);
        $('#checklistProgressFill').css('width', `${progressPercent}%`);
        
        // Update each step
        this.steps.forEach((step, index) => {
            const $step = $(`.checklist-step[data-step="${step}"]`);
            const isComplete = this.isStepComplete(step);
            const isActive = !isComplete && this.getNextIncompleteStep() === step;
            
            $step.removeClass('completed active');
            
            if (isComplete) {
                $step.addClass('completed');
                $step.find('.step-pending').addClass('d-none');
                $step.find('.step-complete').removeClass('d-none');
            } else {
                $step.find('.step-pending').removeClass('d-none');
                $step.find('.step-complete').addClass('d-none');
                
                if (isActive) {
                    $step.addClass('active');
                }
            }
        });
        
        // Update continue button
        const nextStep = this.getNextIncompleteStep();
        const buttonLabels = {
            'connection': 'Create Connection',
            'dictionary': 'Build Dictionary',
            'agent': 'Create Agent'
        };
        
        if (nextStep) {
            $('#continueButtonText').text(buttonLabels[nextStep] || 'Continue');
        } else {
            $('#continueButtonText').text('Complete!');
        }
    }
    
    highlightCurrentPage() {
        const path = window.location.pathname;
        let currentPage = '';
        
        if (path.includes('connections') || path.includes('universal_connections')) {
            currentPage = 'connections';
        } else if (path.includes('data_dictionary')) {
            currentPage = 'data-dictionary';
        } else if (path.includes('custom_data_agent')) {
            currentPage = 'data-agent-builder';
        }
        
        if (currentPage) {
            $('body').attr('data-page', currentPage);
        }
    }
    
    showCompletionToast() {
        $('#dataAssistantChecklist').addClass('d-none');
        $('#dataAssistantComplete').removeClass('d-none');
        
        // Auto-hide after 10 seconds
        setTimeout(() => {
            this.hideCompletionToast();
        }, 10000);
    }
    
    hideCompletionToast() {
        $('#dataAssistantComplete').addClass('d-none');
        // Mark as fully complete
        this.state.active = false;
        this.saveState();
    }
    
    // =========================================================================
    // Navigation
    // =========================================================================
    
    navigateToNextStep() {
        const nextStep = this.getNextIncompleteStep();
        
        const routes = {
            'connection': '/connections',
            'dictionary': '/data_dictionary',
            'agent': '/custom_data_agent'
        };
        
        if (nextStep && routes[nextStep]) {
            window.location.href = routes[nextStep];
        }
    }
    
    toggle() {
        this.isCollapsed = !this.isCollapsed;
        $('#dataAssistantChecklist').toggleClass('collapsed', this.isCollapsed);
    }
}


// =============================================================================
// Global Instance & Functions
// =============================================================================

let dataAssistantChecklist = null;

// Initialize on page load
$(document).ready(function() {
    dataAssistantChecklist = new DataAssistantChecklist();
});

/**
 * Toggle checklist expand/collapse
 */
function toggleChecklist() {
    if (dataAssistantChecklist) {
        dataAssistantChecklist.toggle();
    }
}

/**
 * Continue to next step
 */
function continueDataAssistantSetup() {
    if (dataAssistantChecklist) {
        dataAssistantChecklist.navigateToNextStep();
    }
}

/**
 * Dismiss the checklist
 */
function dismissDataAssistantChecklist() {
    if (dataAssistantChecklist) {
        dataAssistantChecklist.dismiss();
    }
}

/**
 * Hide completion toast
 */
function hideCompletionToast() {
    if (dataAssistantChecklist) {
        dataAssistantChecklist.hideCompletionToast();
    }
}

/**
 * Go to Data Assistant chat page after completion
 */
function goToDataAssistantChat() {
    if (dataAssistantChecklist) {
        dataAssistantChecklist.hideCompletionToast();
    }
    window.location.href = '/data_assistants';
}

/**
 * Activate the Data Assistant checklist (called from onboarding flow)
 */
function activateDataAssistantChecklist() {
    if (dataAssistantChecklist) {
        dataAssistantChecklist.activate();
    } else {
        // Store in session, will be picked up on next page
        sessionStorage.setItem('activateDataAssistantChecklist', 'true');
    }
}

/**
 * Mark a step as complete (call from your existing code when actions complete)
 * 
 * Usage:
 *   markDataAssistantStep('connection');  // When connection is saved
 *   markDataAssistantStep('dictionary');  // When dictionary is saved
 *   markDataAssistantStep('agent');       // When data agent is created
 *   markDataAssistantStep('query');       // When first chat message is sent
 */
function markDataAssistantStep(stepName) {
    if (dataAssistantChecklist) {
        dataAssistantChecklist.completeStep(stepName);
    }
}

/**
 * Check if checklist is active (useful for conditional UI)
 */
function isDataAssistantChecklistActive() {
    return dataAssistantChecklist && dataAssistantChecklist.state.active;
}


// =============================================================================
// Auto-detect step completion based on page
// =============================================================================

$(document).ready(function() {
    // Check for activation trigger from session storage
    if (sessionStorage.getItem('activateDataAssistantChecklist') === 'true') {
        sessionStorage.removeItem('activateDataAssistantChecklist');
        setTimeout(() => activateDataAssistantChecklist(), 500);
    }
});
