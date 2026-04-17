/**
 * AI Hub Developer Onboarding System
 * 
 * Handles developer quick start modal, build path selection, and guided tours
 * using Shepherd.js for step-by-step walkthroughs.
 * 
 * Build Paths:
 * - custom-agent: Create agent with custom tools
 * - data-agent: Data Assistant / NLQ setup
 * - workflow: Workflow automation builder
 * - import: Import existing agent package
 * - explore: General platform tour
 */

class OnboardingManager {
    constructor(options = {}) {
        this.currentStep = 1;
        this.totalSteps = 3;
        this.selectedPath = null;
        this.tour = null;
        this.pageTour = null;
        
        // Options
        this.autoStart = options.autoStart !== false;
        this.currentPage = options.currentPage || this.detectCurrentPage();
        
        // Initialize if on supported page
        if (this.autoStart) {
            this.init();
        }
    }
    
    detectCurrentPage() {
        const path = window.location.pathname;
        if (path === '/' || path === '/dashboard') return 'dashboard';
        if (path.includes('custom_agent_enhanced')) return 'agent-builder';
        if (path.includes('custom_data_agent')) return 'data-agent-builder';
        if (path.includes('workflow_tool')) return 'workflow-builder';
        if (path.includes('custom_tool')) return 'tool-builder';
        if (path.includes('assistants')) return 'chat';
        if (path.includes('data_assistants')) return 'data-chat';
        if (path.includes('connections')) return 'connections';
        return 'other';
    }
    
    async init() {
        try {
            const status = await this.checkOnboardingStatus();
            
            // Dashboard: show welcome modal if new user
            if (this.currentPage === 'dashboard' && status.needs_onboarding) {
                this.showWelcomeModal();
                this.bindModalEvents();
            }
            // Other pages: offer page-specific tour if not taken
            else if (this.currentPage !== 'dashboard' && this.currentPage !== 'other') {
                const hasTakenPageTour = await this.checkPageTour(this.currentPage);
                if (!hasTakenPageTour) {
                    this.showPageTourPrompt();
                }
            }
        } catch (error) {
            console.error('Onboarding init error:', error);
        }
    }
    
    // =========================================================================
    // API Methods
    // =========================================================================
    
    async checkOnboardingStatus() {
        try {
            const response = await fetch('/api/onboarding/status');
            const data = await response.json();
            return data.success ? data : { needs_onboarding: false };
        } catch (error) {
            console.error('Error checking onboarding status:', error);
            return { needs_onboarding: false };
        }
    }
    
    async checkPageTour(tourName) {
        try {
            const response = await fetch(`/api/onboarding/tour/check/${tourName}`);
            const data = await response.json();
            return data.has_taken || false;
        } catch (error) {
            return false;
        }
    }
    
    async saveProgress(step, path = null) {
        try {
            await fetch('/api/onboarding/progress', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ step, goal: path })
            });
        } catch (error) {
            console.error('Error saving progress:', error);
        }
    }
    
    async completeOnboarding(viaTour = false) {
        try {
            await fetch('/api/onboarding/complete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ via_tour: viaTour })
            });
        } catch (error) {
            console.error('Error completing onboarding:', error);
        }
    }
    
    async skipOnboarding() {
        try {
            await fetch('/api/onboarding/skip', { method: 'POST' });
            $('#welcomeModal').modal('hide');
        } catch (error) {
            $('#welcomeModal').modal('hide');
        }
    }
    
    async recordPageTour(tourName) {
        try {
            await fetch('/api/onboarding/tour/record', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tour_name: tourName })
            });
        } catch (error) {
            console.error('Error recording tour:', error);
        }
    }
    
    // =========================================================================
    // Welcome Modal
    // =========================================================================
    
    showWelcomeModal() {
        $('#welcomeModal').modal('show');
    }
    
    bindModalEvents() {
        // Navigation
        $('#btnWelcomeNext').off('click').on('click', () => this.nextStep());
        $('#btnWelcomePrev').off('click').on('click', () => this.prevStep());
        $('#btnCloseWelcome').off('click').on('click', () => this.skipOnboarding());
        $('#btnSkipOnboarding').off('click').on('click', () => this.skipOnboarding());
        
        // Build path selection
        $('.build-path-card').off('click').on('click', (e) => {
            const card = $(e.currentTarget);
            $('.build-path-card').removeClass('selected');
            card.addClass('selected');
            this.selectedPath = card.data('path');
        });
        
        // Just explore button
        $('#btnJustExplore').off('click').on('click', () => {
            this.selectedPath = 'explore';
            this.nextStep();
        });
    }
    
    nextStep() {
        // Validation: must select path on step 2
        if (this.currentStep === 2 && !this.selectedPath) {
            // Highlight cards briefly
            $('.build-path-card').addClass('border-warning');
            setTimeout(() => $('.build-path-card').removeClass('border-warning'), 500);
            return;
        }
        
        if (this.currentStep < this.totalSteps) {
            this.currentStep++;
            this.updateStepDisplay();
            this.saveProgress(this.currentStep, this.selectedPath);
        } else {
            // Final step - take action based on path
            this.executeSelectedPath();
        }
    }
    
    prevStep() {
        if (this.currentStep > 1) {
            this.currentStep--;
            this.updateStepDisplay();
        }
    }
    
    updateStepDisplay() {
        // Hide all steps, show current
        $('.welcome-step').addClass('d-none');
        $(`.welcome-step[data-step="${this.currentStep}"]`).removeClass('d-none');
        
        // Update path-specific content on step 3
        if (this.currentStep === 3 && this.selectedPath) {
            $('.path-quickstart').addClass('d-none');
            $(`.path-quickstart[data-path="${this.selectedPath}"]`).removeClass('d-none');
        }
        
        // Navigation buttons
        $('#btnWelcomePrev').css('visibility', this.currentStep > 1 ? 'visible' : 'hidden');
        
        // Update next button text
        if (this.currentStep === 3) {
            const labels = {
                'custom-agent': '<i class="fas fa-rocket mr-1"></i> Start Building',
                'data-agent': '<i class="fas fa-database mr-1"></i> Connect Data',
                'workflow': '<i class="fas fa-project-diagram mr-1"></i> Open Builder',
                'import': '<i class="fas fa-file-import mr-1"></i> Import Agent',
                'explore': '<i class="fas fa-play mr-1"></i> Start Tour'
            };
            $('#btnWelcomeNext').html(labels[this.selectedPath] || 'Continue');
            $('#btnSkipOnboarding').removeClass('d-none');
        } else {
            $('#btnWelcomeNext').html('Get Started <i class="fas fa-arrow-right ml-1"></i>');
            $('#btnSkipOnboarding').addClass('d-none');
        }
    }
    
    executeSelectedPath() {
        this.completeOnboarding(false);
        $('#welcomeModal').modal('hide');
        
        setTimeout(() => {
            switch (this.selectedPath) {
                case 'custom-agent':
                    window.location.href = '/custom_agent_enhanced';
                    break;
                case 'data-agent':
                    // Activate the Data Assistant checklist, then navigate to connections
                    fetch('/api/onboarding/checklist/data-assistant/activate', { method: 'POST' })
                        .finally(() => {
                            // Start at connections page (step 1 of the flow)
                            window.location.href = '/connections';
                        });
                    return; // Don't fall through
                case 'workflow':
                    window.location.href = '/workflow_tool';
                    break;
                case 'import':
                    window.location.href = '/custom_agent_enhanced';
                    // Trigger import dialog after page loads
                    sessionStorage.setItem('triggerImport', 'true');
                    break;
                case 'explore':
                    this.startDashboardTour();
                    break;
                default:
                    // Stay on dashboard
                    break;
            }
        }, 300);
    }
    
    // =========================================================================
    // Dashboard Tour
    // =========================================================================
    
    startDashboardTour() {
        $('#welcomeModal').modal('hide');
        
        setTimeout(() => {
            this.initDashboardTour();
            this.tour.start();
        }, 400);
    }
    
    initDashboardTour() {
        this.tour = new Shepherd.Tour({
            useModalOverlay: true,
            defaultStepOptions: {
                cancelIcon: { enabled: true },
                classes: 'shepherd-theme-custom',
                scrollTo: { behavior: 'smooth', block: 'center' }
            }
        });
        
        // Step 1: Dashboard overview
        this.tour.addStep({
            id: 'dashboard-overview',
            title: '// Dashboard Overview',
            text: `
                <p>This is your command center. From here you can access all your agents, conversations, and quick actions.</p>
                <p>Think of it as your AI agent control panel.</p>
            `,
            attachTo: { element: '.welcome-section', on: 'bottom' },
            buttons: [
                { text: 'Next →', action: this.tour.next, classes: 'shepherd-button-primary' }
            ]
        });
        
        // Step 2: Action cards (if visible)
        const actionGrid = document.querySelector('.action-grid');
        if (actionGrid) {
            this.tour.addStep({
                id: 'action-cards',
                title: '// Quick Actions',
                text: `
                    <p>These cards give you quick access to common tasks:</p>
                    <ul>
                        <li><strong>Chat with Agent</strong> - Test and interact with your agents</li>
                        <li><strong>Build Agent</strong> - Create new AI agents</li>
                        <li><strong>Build Workflow</strong> - Visual automation builder</li>
                        <li><strong>Create Tool</strong> - Write custom Python tools</li>
                    </ul>
                `,
                attachTo: { element: '.action-grid', on: 'bottom' },
                buttons: [
                    { text: '← Back', action: this.tour.back, classes: 'shepherd-button-secondary' },
                    { text: 'Next →', action: this.tour.next, classes: 'shepherd-button-primary' }
                ]
            });
        }
        
        // Step 3: Agents section (handles both developer and user views)
        const agentsElement = document.getElementById('agentsList') || document.getElementById('assistantsList');
        if (agentsElement) {
            this.tour.addStep({
                id: 'agents-section',
                title: '// Your Agents',
                text: `
                    <p>All agents you build appear here. Each agent can have:</p>
                    <ul>
                        <li><strong>Custom tools</strong> - Python functions the agent can call</li>
                        <li><strong>Knowledge</strong> - Documents for RAG retrieval</li>
                        <li><strong>Environment</strong> - Python dependencies</li>
                    </ul>
                    <p>Click any agent to chat with it or edit its configuration.</p>
                `,
                attachTo: { element: agentsElement, on: 'top' },
                buttons: [
                    { text: '← Back', action: this.tour.back, classes: 'shepherd-button-secondary' },
                    { text: 'Next →', action: this.tour.next, classes: 'shepherd-button-primary' }
                ]
            });
        }
        
        // Step 4: Navigation
        this.tour.addStep({
            id: 'navigation',
            title: '// Navigation',
            text: `
                <p>The sidebar gives you access to everything:</p>
                <ul>
                    <li><strong>AI Agents</strong> - Build and chat with agents</li>
                    <li><strong>Agent Jobs</strong> - Schedule automated runs</li>
                    <li><strong>Workflows</strong> - Visual automation builder</li>
                    <li><strong>Custom Tools</strong> - Write Python tools</li>
                    <li><strong>Connections</strong> - Database & API setup</li>
                </ul>
            `,
            attachTo: { element: '.new-sidebar-nav', on: 'right' },
            buttons: [
                { text: '← Back', action: this.tour.back, classes: 'shepherd-button-secondary' },
                { text: 'Next →', action: this.tour.next, classes: 'shepherd-button-primary' }
            ]
        });
        
        // Step 5: Export/Import callout
        this.tour.addStep({
            id: 'portability',
            title: '// Portable Agents',
            text: `
                <p>Every agent you build can be <strong>exported</strong> as a package containing:</p>
                <ul>
                    <li>Agent configuration</li>
                    <li>Custom tool code</li>
                    <li>Environment dependencies</li>
                </ul>
                <p>Share with clients, version control them, or upload to a marketplace.</p>
                <div class="tip">
                    <i class="fas fa-lightbulb"></i>
                    Export from Agent Builder → Export Agent
                </div>
            `,
            buttons: [
                { text: '← Back', action: this.tour.back, classes: 'shepherd-button-secondary' },
                { text: 'Next →', action: this.tour.next, classes: 'shepherd-button-primary' }
            ]
        });
        
        // Step 6: CTA
        this.tour.addStep({
            id: 'next-steps',
            title: '// Ready to Build?',
            text: `
                <p>You now know the basics. Here's what to do next:</p>
                <ol>
                    <li>Create an agent in <strong>Agent Builder</strong></li>
                    <li>Add tools from the built-in library or write your own</li>
                    <li>Test it in <strong>Agent Chat</strong></li>
                    <li>Export and deploy!</li>
                </ol>
                <p>Need to see this tour again? Find it in the Help menu.</p>
            `,
            buttons: [
                { text: '← Back', action: this.tour.back, classes: 'shepherd-button-secondary' },
                {
                    text: 'Open Agent Builder',
                    action: () => {
                        this.completeOnboarding(true);
                        this.recordPageTour('dashboard');
                        window.location.href = '/custom_agent_enhanced';
                    },
                    classes: 'shepherd-button-primary'
                },
                {
                    text: 'Done',
                    action: () => {
                        this.completeOnboarding(true);
                        this.recordPageTour('dashboard');
                        this.tour.complete();
                    },
                    classes: 'shepherd-button-secondary'
                }
            ]
        });
        
        this.tour.on('complete', () => {
            this.completeOnboarding(true);
            this.recordPageTour('dashboard');
        });
        
        this.tour.on('cancel', () => {
            this.completeOnboarding(true);
            this.recordPageTour('dashboard');
        });
    }
    
    // =========================================================================
    // Agent Builder Tour
    // =========================================================================
    
    startAgentBuilderTour() {
        this.dismissPageTourPrompt();
        
        setTimeout(() => {
            this.initAgentBuilderTour();
            this.pageTour.start();
        }, 200);
    }
    
    initAgentBuilderTour() {
        this.pageTour = new Shepherd.Tour({
            useModalOverlay: true,
            defaultStepOptions: {
                cancelIcon: { enabled: true },
                classes: 'shepherd-theme-custom',
                scrollTo: { behavior: 'smooth', block: 'center' }
            }
        });
        
        // Step 1: Intro
        this.pageTour.addStep({
            id: 'builder-intro',
            title: '// Agent Builder',
            text: `
                <p>This is where you create and configure AI agents.</p>
                <p>An agent = <strong>Objective</strong> + <strong>Tools</strong> + <strong>Knowledge</strong></p>
            `,
            attachTo: { element: '.agent-builder h1', on: 'bottom' },
            buttons: [
                { text: 'Next →', action: this.pageTour.next, classes: 'shepherd-button-primary' }
            ]
        });
        
        // Step 2: Agent selector
        const agentDropdown = document.querySelector('#agent-dropdown');
        if (agentDropdown) {
            this.pageTour.addStep({
                id: 'agent-selector',
                title: '// Select or Create',
                text: `
                    <p>Use the dropdown to edit existing agents, or click <strong>Add New Agent</strong> to create one.</p>
                    <div class="tip">
                        <i class="fas fa-lightbulb"></i>
                        Start with a simple agent, then add complexity.
                    </div>
                `,
                attachTo: { element: '#agent-dropdown', on: 'left' },
                buttons: [
                    { text: '← Back', action: this.pageTour.back, classes: 'shepherd-button-secondary' },
                    { text: 'Next →', action: this.pageTour.next, classes: 'shepherd-button-primary' }
                ]
            });
        }
        
        // Step 3: Objective
        this.pageTour.addStep({
            id: 'agent-objective',
            title: '// Agent Objective',
            text: `
                <p>The <strong>objective</strong> is the system prompt that defines your agent's behavior.</p>
                <p>Be specific about:</p>
                <ul>
                    <li>What the agent should do</li>
                    <li>How it should respond</li>
                    <li>What tools it should use when</li>
                </ul>
                <div class="tip">
                    <i class="fas fa-lightbulb"></i>
                    Good objectives = better agent performance
                </div>
            `,
            attachTo: { element: '#objective', on: 'bottom' },
            buttons: [
                { text: '← Back', action: this.pageTour.back, classes: 'shepherd-button-secondary' },
                { text: 'Next →', action: this.pageTour.next, classes: 'shepherd-button-primary' }
            ]
        });
        
        // Step 4: Tools
        this.pageTour.addStep({
            id: 'tools-section',
            title: '// Tools',
            text: `
                <p><strong>Tools</strong> are functions your agent can call. This is what makes AI Hub different from ChatGPT.</p>
                <p>Tool types:</p>
                <ul>
                    <li><strong>Core Tools</strong> - Built-in (web search, calculator, etc.)</li>
                    <li><strong>Custom Tools</strong> - Your Python code</li>
                </ul>
                <p>Select tools by checking the boxes. Only enable what the agent needs.</p>
            `,
            attachTo: { element: '#tools-by-category', on: 'right' },
            buttons: [
                { text: '← Back', action: this.pageTour.back, classes: 'shepherd-button-secondary' },
                { text: 'Next →', action: this.pageTour.next, classes: 'shepherd-button-primary' }
            ]
        });
        
        // Step 5: Actions panel
        this.pageTour.addStep({
            id: 'actions-panel',
            title: '// Agent Actions',
            text: `
                <p>After creating an agent:</p>
                <ul>
                    <li><strong>Manage Knowledge</strong> - Attach documents for RAG</li>
                    <li><strong>Export Agent</strong> - Package for deployment</li>
                    <li><strong>Import Agent</strong> - Load a package</li>
                </ul>
                <p>Exported agents include all tools and dependencies.</p>
            `,
            attachTo: { element: '.card:has(#exportAgentBtn)', on: 'left' },
            buttons: [
                { text: '← Back', action: this.pageTour.back, classes: 'shepherd-button-secondary' },
                { text: 'Next →', action: this.pageTour.next, classes: 'shepherd-button-primary' }
            ]
        });
        
        // Step 6: Save
        this.pageTour.addStep({
            id: 'save-agent',
            title: '// Save & Test',
            text: `
                <p>Click <strong>Save Changes</strong> to save your agent.</p>
                <p>Then test it in <strong>Agent Chat</strong> from the sidebar.</p>
                <div class="tip">
                    <i class="fas fa-lightbulb"></i>
                    Build iteratively: create → test → refine → repeat
                </div>
            `,
            attachTo: { element: 'button[onclick="updateAgent()"]', on: 'top' },
            buttons: [
                { text: '← Back', action: this.pageTour.back, classes: 'shepherd-button-secondary' },
                {
                    text: 'Got It',
                    action: () => {
                        this.recordPageTour('agent-builder');
                        this.pageTour.complete();
                    },
                    classes: 'shepherd-button-primary'
                }
            ]
        });
        
        this.pageTour.on('complete', () => this.recordPageTour('agent-builder'));
        this.pageTour.on('cancel', () => this.recordPageTour('agent-builder'));
    }
    
    // =========================================================================
    // Custom Tool Builder Tour
    // =========================================================================
    
    startToolBuilderTour() {
        this.dismissPageTourPrompt();
        
        setTimeout(() => {
            this.initToolBuilderTour();
            this.pageTour.start();
        }, 200);
    }
    
    initToolBuilderTour() {
        this.pageTour = new Shepherd.Tour({
            useModalOverlay: true,
            defaultStepOptions: {
                cancelIcon: { enabled: true },
                classes: 'shepherd-theme-custom',
                scrollTo: { behavior: 'smooth', block: 'center' }
            }
        });
        
        this.pageTour.addStep({
            id: 'tool-intro',
            title: '// Custom Tools',
            text: `
                <p>Custom tools let you extend agent capabilities with Python code.</p>
                <p>A tool is a Python function that:</p>
                <ul>
                    <li>Takes parameters defined in a schema</li>
                    <li>Does something (API call, calculation, etc.)</li>
                    <li>Returns a result the agent can use</li>
                </ul>
            `,
            buttons: [
                { text: 'Next →', action: this.pageTour.next, classes: 'shepherd-button-primary' }
            ]
        });
        
        this.pageTour.addStep({
            id: 'tool-structure',
            title: '// Tool Structure',
            text: `
                <p>Every tool needs:</p>
                <ul>
                    <li><strong>Name</strong> - How the agent calls it</li>
                    <li><strong>Description</strong> - When to use it</li>
                    <li><strong>Parameters</strong> - What inputs it takes</li>
                    <li><strong>Code</strong> - What it does</li>
                </ul>
                <div class="tour-code-block">
                    <code>
                        <span class="keyword">def</span> my_tool(param1, param2):<br>
                        &nbsp;&nbsp;<span class="comment"># Your logic here</span><br>
                        &nbsp;&nbsp;<span class="keyword">return</span> result
                    </code>
                </div>
            `,
            buttons: [
                { text: '← Back', action: this.pageTour.back, classes: 'shepherd-button-secondary' },
                {
                    text: 'Got It',
                    action: () => {
                        this.recordPageTour('tool-builder');
                        this.pageTour.complete();
                    },
                    classes: 'shepherd-button-primary'
                }
            ]
        });
        
        this.pageTour.on('complete', () => this.recordPageTour('tool-builder'));
        this.pageTour.on('cancel', () => this.recordPageTour('tool-builder'));
    }
    
    // =========================================================================
    // Workflow Builder Tour
    // =========================================================================
    
    startWorkflowBuilderTour() {
        this.dismissPageTourPrompt();
        
        setTimeout(() => {
            this.initWorkflowBuilderTour();
            this.pageTour.start();
        }, 200);
    }
    
    initWorkflowBuilderTour() {
        this.pageTour = new Shepherd.Tour({
            useModalOverlay: true,
            defaultStepOptions: {
                cancelIcon: { enabled: true },
                classes: 'shepherd-theme-custom',
                scrollTo: { behavior: 'smooth', block: 'center' }
            }
        });
        
        // Step 1: Welcome to Workflow Designer
        this.pageTour.addStep({
            id: 'workflow-intro',
            title: '// Workflow Designer',
            text: `
                <p>Welcome to the <strong>Workflow Designer</strong> - your visual automation builder.</p>
                <p>Here you can create multi-step processes that chain together:</p>
                <ul>
                    <li>Data sources (databases, files)</li>
                    <li>AI actions and extractions</li>
                    <li>Logic and conditionals</li>
                    <li>Alerts and approvals</li>
                </ul>
            `,
            buttons: [
                { text: 'Next →', action: this.pageTour.next, classes: 'shepherd-button-primary' }
            ]
        });
        
        // Step 2: Workflow Header/Controls
        this.pageTour.addStep({
            id: 'workflow-header',
            title: '// Workflow Controls',
            text: `
                <p>The header bar lets you manage workflows:</p>
                <ul>
                    <li><strong>New</strong> - Create a blank workflow</li>
                    <li><strong>Select + Load</strong> - Open a saved workflow</li>
                    <li><strong>Manage</strong> - Browse all workflows</li>
                    <li><strong>Variables</strong> - Define workflow variables</li>
                </ul>
            `,
            attachTo: { element: '.workflow-header', on: 'bottom' },
            buttons: [
                { text: '← Back', action: this.pageTour.back, classes: 'shepherd-button-secondary' },
                { text: 'Next →', action: this.pageTour.next, classes: 'shepherd-button-primary' }
            ]
        });
        
        // Step 3: Run Controls
        this.pageTour.addStep({
            id: 'workflow-run',
            title: '// Run Your Workflow',
            text: `
                <p>Once you've built a workflow, click <strong>Run</strong> to execute it.</p>
                <p>You'll see real-time status updates as each node executes.</p>
                <div class="tip">
                    <i class="fas fa-lightbulb"></i>
                    Workflows can also be scheduled to run automatically via Agent Jobs.
                </div>
            `,
            attachTo: { element: '#runWorkflowBtn', on: 'bottom' },
            buttons: [
                { text: '← Back', action: this.pageTour.back, classes: 'shepherd-button-secondary' },
                { text: 'Next →', action: this.pageTour.next, classes: 'shepherd-button-primary' }
            ]
        });
        
        // Step 4: Node Toolbox
        this.pageTour.addStep({
            id: 'workflow-toolbar',
            title: '// Node Toolbox',
            text: `
                <p>The toolbox contains all available node types. <strong>Drag and drop</strong> them onto the canvas.</p>
                <p>Nodes are organized by category:</p>
                <ul>
                    <li><strong>Data Sources</strong> - Database, File, Folder</li>
                    <li><strong>AI & Documents</strong> - AI Action, AI Extract</li>
                    <li><strong>Flow Control</strong> - Conditionals, Loops</li>
                    <li><strong>Communication</strong> - Alerts, Approvals</li>
                </ul>
            `,
            attachTo: { element: '.toolbar', on: 'right' },
            buttons: [
                { text: '← Back', action: this.pageTour.back, classes: 'shepherd-button-secondary' },
                { text: 'Next →', action: this.pageTour.next, classes: 'shepherd-button-primary' }
            ]
        });
        
        // Step 5: Data Source Nodes
        this.pageTour.addStep({
            id: 'workflow-data-sources',
            title: '// Data Source Nodes',
            text: `
                <p><strong>Database</strong> - Query any connected database</p>
                <p><strong>File</strong> - Read from CSV, Excel, JSON, or text files</p>
                <p><strong>Folder Selector</strong> - Process multiple files from a directory</p>
                <div class="tip">
                    <i class="fas fa-lightbulb"></i>
                    Data flows from node to node - each step can use output from previous steps.
                </div>
            `,
            attachTo: { element: '.tool-item[data-type="Database"]', on: 'right' },
            buttons: [
                { text: '← Back', action: this.pageTour.back, classes: 'shepherd-button-secondary' },
                { text: 'Next →', action: this.pageTour.next, classes: 'shepherd-button-primary' }
            ]
        });
        
        // Step 6: AI Nodes
        this.pageTour.addStep({
            id: 'workflow-ai-nodes',
            title: '// AI-Powered Nodes',
            text: `
                <p><strong>AI Action</strong> - Send data to an AI agent for processing, analysis, or transformation.</p>
                <p><strong>AI Extract</strong> - Extract structured data from documents using AI (invoices, forms, etc.).</p>
                <p>These nodes connect your workflows to the power of your custom agents.</p>
            `,
            attachTo: { element: '.tool-item[data-type="AI Action"]', on: 'right' },
            buttons: [
                { text: '← Back', action: this.pageTour.back, classes: 'shepherd-button-secondary' },
                { text: 'Next →', action: this.pageTour.next, classes: 'shepherd-button-primary' }
            ]
        });
        
        // Step 7: Flow Control
        this.pageTour.addStep({
            id: 'workflow-flow-control',
            title: '// Flow Control',
            text: `
                <p><strong>Conditional</strong> - Branch based on conditions (if/then/else)</p>
                <p><strong>Loop</strong> - Repeat steps for each item in a list</p>
                <p>These let you build complex logic without writing code.</p>
            `,
            attachTo: { element: '.tool-item[data-type="Conditional"]', on: 'right' },
            buttons: [
                { text: '← Back', action: this.pageTour.back, classes: 'shepherd-button-secondary' },
                { text: 'Next →', action: this.pageTour.next, classes: 'shepherd-button-primary' }
            ]
        });
        
        // Step 8: Canvas
        this.pageTour.addStep({
            id: 'workflow-canvas',
            title: '// The Canvas',
            text: `
                <p>This is where you build your workflow visually.</p>
                <p><strong>How to build:</strong></p>
                <ol>
                    <li>Drag nodes from the toolbox onto the canvas</li>
                    <li>Connect nodes by dragging from one node's edge to another</li>
                    <li>Double-click a node to configure it</li>
                    <li>Right-click for more options (rename, delete, etc.)</li>
                </ol>
            `,
            attachTo: { element: '#workflow-canvas', on: 'left' },
            buttons: [
                { text: '← Back', action: this.pageTour.back, classes: 'shepherd-button-secondary' },
                { text: 'Next →', action: this.pageTour.next, classes: 'shepherd-button-primary' }
            ]
        });
        
        // Step 9: Save Button
        this.pageTour.addStep({
            id: 'workflow-save',
            title: '// Save Your Work',
            text: `
                <p>Click <strong>Save</strong> to save your workflow.</p>
                <p>Saved workflows can be:</p>
                <ul>
                    <li>Loaded and edited later</li>
                    <li>Scheduled as automated jobs</li>
                    <li>Triggered by other workflows</li>
                </ul>
            `,
            attachTo: { element: '.btn-save', on: 'bottom' },
            buttons: [
                { text: '← Back', action: this.pageTour.back, classes: 'shepherd-button-secondary' },
                { text: 'Next →', action: this.pageTour.next, classes: 'shepherd-button-primary' }
            ]
        });
        
        // Step 10: Get Started
        this.pageTour.addStep({
            id: 'workflow-start',
            title: '// Ready to Build!',
            text: `
                <p>You're ready to create your first workflow.</p>
                <p><strong>Try this:</strong></p>
                <ol>
                    <li>Click <strong>New</strong> to start fresh</li>
                    <li>Drag a <strong>Database</strong> node onto the canvas</li>
                    <li>Drag an <strong>AI Action</strong> node and connect them</li>
                    <li>Configure each node and click <strong>Run</strong></li>
                </ol>
                <div class="tip">
                    <i class="fas fa-lightbulb"></i>
                    Need help? Right-click nodes for options, or check the docs.
                </div>
            `,
            buttons: [
                { text: '← Back', action: this.pageTour.back, classes: 'shepherd-button-secondary' },
                {
                    text: 'Start Building!',
                    action: () => {
                        this.recordPageTour('workflow-builder');
                        this.pageTour.complete();
                    },
                    classes: 'shepherd-button-primary'
                }
            ]
        });
        
        this.pageTour.on('complete', () => this.recordPageTour('workflow-builder'));
        this.pageTour.on('cancel', () => this.recordPageTour('workflow-builder'));
    }
    
    // =========================================================================
    // Page Tour Prompt
    // =========================================================================
    
    showPageTourPrompt() {
        const messages = {
            'agent-builder': 'Learn how to build agents with custom tools.',
            'data-agent-builder': 'Learn how to create data assistants with NLQ.',
            'workflow-builder': 'Learn how to automate with visual workflows.',
            'tool-builder': 'Learn how to write custom Python tools.',
            'chat': 'Learn how to test and interact with your agents.',
            'connections': 'Learn how to connect databases and APIs.'
        };
        
        const message = messages[this.currentPage] || 'Take a quick tour of this page.';
        $('#tourPromptMessage').text(message);
        
        $('#btnStartPageTour').off('click').on('click', () => {
            switch (this.currentPage) {
                case 'agent-builder':
                    this.startAgentBuilderTour();
                    break;
                case 'tool-builder':
                    this.startToolBuilderTour();
                    break;
                case 'workflow-builder':
                    this.startWorkflowBuilderTour();
                    break;
                default:
                    this.dismissPageTourPrompt();
            }
        });
        
        $('#pageTourPrompt').removeClass('d-none');
    }
    
    dismissPageTourPrompt() {
        $('#pageTourPrompt').addClass('d-none');
    }
}


// =============================================================================
// Global Functions
// =============================================================================

/**
 * Replay onboarding from help menu
 */
function replayOnboardingTour() {
    fetch('/api/onboarding/reset', { method: 'POST' })
        .then(response => response.json())
        .then(() => {
            window.location.href = '/';
        })
        .catch(error => {
            console.error('Error resetting onboarding:', error);
            alert('Could not reset tour. Please try again.');
        });
}

/**
 * Start a specific page tour manually
 */
function startPageTour(tourName) {
    if (window.onboardingManager) {
        switch (tourName) {
            case 'agent-builder':
                window.onboardingManager.startAgentBuilderTour();
                break;
            case 'dashboard':
                window.onboardingManager.startDashboardTour();
                break;
            case 'tool-builder':
                window.onboardingManager.startToolBuilderTour();
                break;
        }
    }
}

/**
 * Dismiss page tour prompt
 */
function dismissPageTourPrompt() {
    if (window.onboardingManager) {
        window.onboardingManager.dismissPageTourPrompt();
    } else {
        $('#pageTourPrompt').addClass('d-none');
    }
}


// =============================================================================
// Auto-Initialize
// =============================================================================

$(document).ready(function() {
    // Initialize on supported pages
    const supportedPages = [
        '/', 
        '/dashboard', 
        '/custom_agent_enhanced', 
        '/custom_data_agent',
        '/workflow_tool',
        '/custom_tool'
    ];
    const currentPath = window.location.pathname;
    
    const isSupported = supportedPages.some(page => 
        currentPath === page || currentPath.startsWith(page + '?')
    );
    
    if (isSupported) {
        window.onboardingManager = new OnboardingManager();
    }
    
    // Check for import trigger (from onboarding flow)
    if (sessionStorage.getItem('triggerImport') === 'true') {
        sessionStorage.removeItem('triggerImport');
        // Trigger import dialog if function exists
        if (typeof showImportDialog === 'function') {
            setTimeout(() => showImportDialog(), 500);
        }
    }
});
