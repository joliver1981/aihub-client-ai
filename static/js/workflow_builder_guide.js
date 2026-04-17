// workflow_builder_guide.js
// Modern UI for AI-guided workflow building with clean, spacious design

class WorkflowBuilderGuide {
    constructor() {
        this.sessionId = Date.now().toString();
        window.workflowBuilderSessionId = this.sessionId;  // Expose for command executor
        this.isOpen = false;
        this.modal = null;
        this.currentPhase = 'discovery';
        this.requirements = {};
        this.trainingCaptureEnabled = window.WORKFLOW_TRAINING_CAPTURE_ENABLED === true;
        this.workflowBuiltSuccessfully = false;
        this.initializeUI();
        console.log('Training capture enabled:', this.trainingCaptureEnabled);
    }
    
    initializeUI() {
        // Try multiple possible locations for the button
        // First, try to add it to the workflow header controls
        let buttonContainer = document.querySelector('.workflow-header .d-flex.align-items-center.gap-2');
        
        // If not found, try the toolbar
        if (!buttonContainer) {
            buttonContainer = document.querySelector('.toolbar');
        }
        
        if (buttonContainer) {
            const guideButton = document.createElement('button');
            guideButton.className = 'btn btn-sm btn-primary workflow-builder-btn ms-2';
            guideButton.innerHTML = `
                <i class="bi bi-magic"></i> AI Builder
            `;
            guideButton.onclick = () => this.open();
            guideButton.title = 'AI Workflow Builder - Create workflows through conversation';
            
            // If it's the workflow header, add it after the Variables button
            const variablesBtn = buttonContainer.querySelector('button[onclick="openWorkflowVariables()"]');
            if (variablesBtn) {
                variablesBtn.insertAdjacentElement('afterend', guideButton);
            } else {
                // Otherwise just append to the container
                buttonContainer.appendChild(guideButton);
            }
            
            console.log('AI Workflow Builder button added to interface');
        } else {
            console.warn('Could not find toolbar or workflow header to add AI Builder button');
        }
        
        // Create modal structure
        this.createModal();
        
        // Add styles
        this.injectStyles();
    }
    
    createModal() {
        const modalHTML = `
        <div class="modal fade" id="workflowBuilderModal" tabindex="-1" data-bs-backdrop="static">
            <div class="modal-dialog modal-xl workflow-builder-dialog">
                <div class="modal-content workflow-builder-content">
                    <!-- Header -->
                    <div class="modal-header workflow-builder-header">
                        <div class="header-content">
                            <h4 class="modal-title">
                                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <path d="M12 2L2 7L12 12L22 7L12 2Z"></path>
                                    <path d="M2 17L12 22L22 17"></path>
                                    <path d="M2 12L12 17L22 12"></path>
                                </svg>
                                AI Workflow Builder
                            </h4>
                            <span class="phase-badge" id="phaseBadge">Discovery</span>
                        </div>
                        <div class="header-actions">
                            <button type="button" class="btn btn-sm btn-outline-light export-training-btn" id="exportTrainingBtn" onclick="workflowBuilder.exportTrainingData()" style="display: none;" title="Export this conversation to training dataset">
                                <i class="bi bi-database-add"></i> Export Training
                            </button>
                            <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                        </div>
                    </div>
                    
                    <!-- Progress Bar -->
                    <div class="progress-container">
                        <div class="progress workflow-progress">
                            <div class="progress-bar" id="progressBar" role="progressbar" style="width: 20%">
                                <span class="progress-label">Discovery</span>
                            </div>
                        </div>
                        <div class="phase-steps">
                            <div class="phase-step active" data-phase="discovery">
                                <div class="step-dot"></div>
                                <span>Discover</span>
                            </div>
                            <div class="phase-step" data-phase="requirements">
                                <div class="step-dot"></div>
                                <span>Gather</span>
                            </div>
                            <div class="phase-step" data-phase="planning">
                                <div class="step-dot"></div>
                                <span>Plan</span>
                            </div>
                            <div class="phase-step" data-phase="building">
                                <div class="step-dot"></div>
                                <span>Build</span>
                            </div>
                            <div class="phase-step" data-phase="refinement">
                                <div class="step-dot"></div>
                                <span>Refine</span>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Main Content Area -->
                    <div class="modal-body workflow-builder-body">
                        <div class="builder-layout">
                            <!-- Chat Section -->
                            <div class="chat-section">
                                <div class="chat-messages" id="builderMessages">
                                    <div class="message assistant fade-in">
                                        <div class="message-avatar">
                                            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                                <circle cx="12" cy="12" r="10"></circle>
                                                <path d="M8 14s1.5 2 4 2 4-2 4-2"></path>
                                                <line x1="9" y1="9" x2="9.01" y2="9"></line>
                                                <line x1="15" y1="9" x2="15.01" y2="9"></line>
                                            </svg>
                                        </div>
                                        <div class="message-content">
                                            <div class="message-header">AI Assistant</div>
                                            <div class="message-text">
                                                Hi! I'm here to help you build a workflow that automates your business process. 
                                                Let's start by understanding what you'd like to automate.
                                                <br><br>
                                                What process or task are you looking to streamline?
                                            </div>
                                        </div>
                                    </div>
                                </div>

                            </div>
                            
                            <!-- Sidebar -->
                            <div class="requirements-sidebar">
                                <div class="sidebar-section">
                                    <h6 class="section-title">
                                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                            <path d="M9 11l3 3L22 4"></path>
                                            <path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"></path>
                                        </svg>
                                        Requirements Gathered
                                    </h6>
                                    <div class="requirements-list" id="requirementsList">
                                        <div class="requirement-item empty">
                                            <span>Requirements will appear here as we discover them...</span>
                                        </div>
                                    </div>
                                </div>
                                
                                <div class="sidebar-section mt-4">
                                    <h6 class="section-title">
                                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                            <circle cx="12" cy="12" r="10"></circle>
                                            <line x1="12" y1="8" x2="12" y2="12"></line>
                                            <line x1="12" y1="16" x2="12.01" y2="16"></line>
                                        </svg>
                                        Quick Tips
                                    </h6>
                                    <div class="tips-list">
                                        <div class="tip-item">
                                            Be specific about your current process</div>
                                        <div class="tip-item">
                                            Mention any systems you use
                                        </div>
                                        <div class="tip-item">
                                            Describe the outcome you want
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Footer -->
                    <div class="modal-footer workflow-builder-footer">
                        <!-- Chat Input (moved here) -->
                        <div class="chat-input-container">
                            <div class="input-wrapper">
                                <textarea 
                                    id="builderInput" 
                                    class="form-control chat-input" 
                                    placeholder="Describe your process or ask a question..."
                                    rows="2"></textarea>
                                <button class="btn btn-primary send-btn" id="sendButton" onclick="workflowBuilder.sendMessage()">
                                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                        <line x1="22" y1="2" x2="11" y2="13"></line>
                                        <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
                                    </svg>
                                </button>
                            </div>
                        </div>
                        
                        <!-- Action Buttons -->
                        <div class="footer-actions">
                            <button class="btn btn-outline-secondary" data-bs-dismiss="modal">Close</button>
                            <button class="btn btn-success build-btn" id="buildWorkflowBtn" style="display:none" onclick="workflowBuilder.buildWorkflow()">
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="me-2">
                                    <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path>
                                </svg>
                                Build Workflow Now
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>`;
        
        document.body.insertAdjacentHTML('beforeend', modalHTML);
        this.modal = new bootstrap.Modal(document.getElementById('workflowBuilderModal'));
        
        // Setup event listeners
        this.setupEventListeners();
    }
    
    setupEventListeners() {
        // Enter key to send message
        const input = document.getElementById('builderInput');
        if (input) {
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    this.sendMessage();
                }
            });
            
            // Auto-resize textarea
            input.addEventListener('input', () => {
                input.style.height = 'auto';
                input.style.height = Math.min(input.scrollHeight, 120) + 'px';
            });
        }
        
        // Modal events
        document.getElementById('workflowBuilderModal').addEventListener('hidden.bs.modal', () => {
            this.isOpen = false;
        });
    }
    
    injectStyles() {
        const styles = `
        <style>
        /* Modern Workflow Builder Styles */
        .workflow-builder-btn {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border: none;
            font-size: 0.875rem;
            font-weight: 500;
            transition: all 0.3s ease;
        }
        
        .workflow-builder-btn:hover {
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);
        }
        
        .workflow-builder-dialog {
            max-width: 1500px;  /* Increased from 1200px */
            width: 95vw;  /* Use more of the viewport width */
            margin: 0.5rem auto;  /* Reduced margin for more space */
        }
        
        .workflow-builder-content {
            border-radius: 20px;  /* Slightly larger radius */
            border: none;
            box-shadow: 0 25px 70px rgba(0, 0, 0, 0.12);  /* Deeper shadow */
            overflow: hidden;
            height: 95vh;  /* Increased from 90vh */
            min-height: 700px;  /* Increased minimum height */
            display: flex;
            flex-direction: column;
            background: #ffffff;
        }
        
        .workflow-builder-header {
            background: linear-gradient(135deg, #2980b9 0%, #34495e 100%);
            border: none;
            padding: 2rem 2.5rem;  /* Increased padding */
            color: white;
            box-shadow: 0 4px 20px rgba(102, 126, 234, 0.15);
        }
        
        .workflow-builder-header .header-content {
            display: flex;
            align-items: center;
            gap: 1.5rem;  /* Increased gap */
            flex: 1;
        }

        .workflow-builder-header .header-actions {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        .workflow-builder-header .export-training-btn {
            font-size: 0.8rem;
            padding: 0.4rem 0.75rem;
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
        }
        
        .workflow-builder-header h4 {
            margin: 0;
            display: flex;
            align-items: center;
            gap: 1rem;  /* Increased gap */
            font-weight: 600;
            font-size: 1.5rem;  /* Larger title */
            letter-spacing: -0.02em;
        }
        
        .phase-badge {
            background: rgba(255, 255, 255, 0.2);
            color: white;
            padding: 6px 16px;
            border-radius: 20px;
            font-size: 0.875rem;
            font-weight: 500;
            backdrop-filter: blur(10px);
        }
        
        /* Progress Section */
        .progress-container {
            background: #f8f9fa;
            padding: 2rem 2.5rem;  /* Increased padding */
            border-bottom: 2px solid #e9ecef;
        }
        
        .workflow-progress {
            height: 10px;  /* Slightly taller progress bar */
            background: #e9ecef;
            border-radius: 12px;
            overflow: visible;
            margin-bottom: 2rem;  /* More space below */
        }
        
        .workflow-progress .progress-bar {
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            border-radius: 12px;
            transition: width 0.6s ease;
            position: relative;
            box-shadow: 0 2px 12px rgba(102, 126, 234, 0.35);
        }
        
        .progress-label {
            position: absolute;
            right: 10px;
            top: -25px;
            font-size: 0.75rem;
            font-weight: 600;
            color: #667eea;
        }
        
        .phase-steps {
            display: flex;
            justify-content: space-between;
            position: relative;
        }
        
        .phase-step {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 0.5rem;
            opacity: 0.5;
            transition: opacity 0.3s ease;
        }
        
        .phase-step.active {
            opacity: 1;
        }
        
        .phase-step .step-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: #dee2e6;
            transition: all 0.3s ease;
        }
        
        .phase-step.active .step-dot {
            background: #667eea;
            box-shadow: 0 0 0 4px rgba(102, 126, 234, 0.2);
        }
        
        .phase-step span {
            font-size: 0.875rem;
            font-weight: 500;
            color: #6c757d;
        }
        
        .phase-step.active span {
            color: #667eea;
        }
        
        /* Main Body Layout */
        .workflow-builder-body {
            flex: 1;
            padding: 0;
            overflow: hidden;
            background: white;
        }
        
        .builder-layout {
            display: grid;
            grid-template-columns: 1fr 420px;  /* Increased sidebar from 380px to 420px, chat gets rest */
            height: 100%;
            gap: 0;
        }
        
        /* Chat Section */
        .chat-section {
            display: flex;
            flex-direction: column;
            border-right: 1px solid #e9ecef;
            background: white;
            height: 100%;
            position: relative;
            min-height: 0; /* Important for Firefox */
        }
        
        .chat-messages {
            flex: 1;
            overflow-y: scroll !important;  /* Force scrollbar */
            overflow-x: hidden;
            padding: 2.5rem 3rem;  /* Increased from 2rem for more spacious feel */
            display: flex;
            flex-direction: column;
            gap: 2rem;  /* Increased from 1.5rem for more space between messages */
            height: 100%;
            min-height: 400px;
            max-height: calc(94vh - 180px);  /* Adjusted for new modal height */
        }
        
        /* Ensure scrollbars are always visible when content overflows */
        .chat-messages::-webkit-scrollbar {
            width: 8px;
            display: block;
        }
        
        .message {
            display: flex;
            gap: 1rem;
            animation: fadeIn 0.3s ease;
        }
        
        .message.user {
            flex-direction: row-reverse;
        }
        
        .message-avatar {
            width: 40px;
            height: 40px;
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }
        
        .message.assistant .message-avatar {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        
        .message.user .message-avatar {
            background: #f1f3f5;
            color: #495057;
        }
        
        .message-content {
            max-width: 85%;  /* Increased from 70% */
            background: #f8f9fa;
            border-radius: 18px;  /* Larger radius */
            padding: 1.25rem 1.5rem;  /* More padding */
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
        }
        
        .message.user .message-content {
            background: #667eea !important;
            color: white !important;
        }
        
        .message.user .message-text {
            color: white !important;  /* Ensure text is white */
        }
        
        .message.user .message-header {
            color: rgba(255, 255, 255, 0.9) !important;  /* Slightly transparent white for header */
        }
        
        .message-header {
            font-size: 0.75rem;
            font-weight: 600;
            margin-bottom: 0.5rem;
            opacity: 0.7;
        }
        
        .message-text {
            padding: 14px 18px;  /* More internal padding */
            border-radius: 14px;
            box-shadow: 0 2px 6px rgba(0, 0, 0, 0.04);
            word-wrap: break-word;
            line-height: 1.6;  /* Better line height */
            font-size: 1rem;  /* Slightly larger text */
            letter-spacing: 0.01em;  /* Slight letter spacing for readability */
        }
        
        /* Different backgrounds for user vs assistant messages */
        .message.assistant .message-text {
            background: white;
        }
        
        .message.user .message-text {
            background: transparent !important;  /* No background, let parent's purple show through */
            color: white !important;
        }
        
        /* Input Area */
        .chat-input-container {
            padding: 2rem 2.5rem;  /* Increased padding */
            background: #e9ecef;
            box-shadow: 0 -5px 20px rgba(0, 0, 0, 0.02);
        }
        
        .input-wrapper {
            display: flex;
            gap: 1rem;  /* Increased gap */
            align-items: flex-end;
        }
        
        .chat-input {
            flex: 1;
            border-radius: 14px;  /* Larger radius */
            border: 2px solid #e9ecef;
            padding: 1rem 1.25rem;  /* More padding */
            resize: none;
            font-size: 1rem;  /* Slightly larger font */
            transition: all 0.2s ease;
            min-height: 56px;  /* Taller minimum */
            max-height: 140px;  /* Allow more expansion */
            background: #f8f9fa;
        }
        
        .chat-input:focus {
            border-color: #667eea;
            box-shadow: 0 0 0 4px rgba(102, 126, 234, 0.1);
            background: white;
        }
        
        .send-btn {
            width: 56px;  /* Larger button */
            height: 56px;
            border-radius: 14px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border: none;
            transition: all 0.2s ease;
        }
        
        .send-btn:hover {
            transform: scale(1.05);
        }
        
        .send-btn:active {
            transform: scale(0.95);
        }
        
        .input-hints {
            margin-top: 0.5rem;
            font-size: 0.75rem;
            color: #6c757d;
        }
        
        /* Sidebar */
        .requirements-sidebar {
            padding: 2.5rem;  /* Increased from 2rem */
            background: #f8f9fa;
            overflow-y: auto;
        }
        
        .sidebar-section {
            margin-bottom: 2.5rem;  /* More space between sections */
        }
        
        .section-title {
            display: flex;
            align-items: center;
            gap: 0.75rem;  /* Increased gap */
            margin-bottom: 1.25rem;  /* More margin */
            font-weight: 600;
            font-size: 1rem;  /* Slightly larger */
            color: #495057;
        }
        
        .requirements-list {
            display: flex;
            flex-direction: column;
            gap: 1rem;  /* More space between items */
        }
        
        .requirement-item {
            background: white;
            padding: 1rem 1.25rem;  /* More padding */
            border-radius: 12px;  /* Larger radius */
            font-size: 0.9rem;  /* Slightly larger text */
            border-left: 3px solid #667eea;
            box-shadow: 0 2px 6px rgba(0, 0, 0, 0.04);
            transition: all 0.2s ease;
        }
        
        .requirement-item:hover {
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
            transform: translateX(2px);
        }
            transition: all 0.2s ease;
        }
        
        .requirement-item.empty {
            border-left-color: #dee2e6;
            color: #6c757d;
            font-style: italic;
        }
        
        .requirement-item:not(.empty):hover {
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
            transform: translateX(2px);
        }
        
        .tips-list {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }
        
        .tip-item {
            padding: 0.5rem 0;
            font-size: 0.875rem;
            color: #6c757d;
            padding-left: 1.25rem;
            position: relative;
        }
        
        .tip-item:before {
            content: "•";
            position: absolute;
            left: 0;
            color: #667eea;
        }
        
        /* Footer */
        /* Chat input container in footer */
        .workflow-builder-footer .chat-input-container {
            flex: 1;
            margin: 0;
            padding: 0;
        }

        .workflow-builder-footer .input-wrapper {
            display: flex;
            gap: 0.5rem;
            align-items: center;
        }

        .workflow-builder-footer .chat-input {
            flex: 1;
            min-height: 38px;
            max-height: 80px;
            resize: none;
            border-radius: 20px;
            padding: 8px 15px;
        }

        .workflow-builder-footer .send-btn {
            height: 38px;
            width: 38px;
            border-radius: 50%;
            padding: 0;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .workflow-builder-footer {
            display: flex;
            align-items: center;
            gap: 1rem;
            padding: 0.75rem 1rem !important;
            border-top: 1px solid #e9ecef;
            background: #e9ecef;
        }

        /* Dark mode overrides for workflow builder */
        .doc-page .workflow-builder-footer {
            background: var(--bg-elevated, #111) !important;
            border-top-color: var(--border-subtle, #1a2333) !important;
        }

        .doc-page .workflow-builder-footer .chat-input {
            background: var(--bg-input, #18181b) !important;
            border-color: var(--border-subtle, #1a2333) !important;
            color: var(--text-primary, #fff) !important;
        }

        .doc-page .workflow-builder-footer .chat-input:focus {
            background: var(--bg-card, #0a0a0a) !important;
            border-color: #667eea !important;
        }

        /* Remove hints in footer - too cramped */
        .workflow-builder-footer .input-hints {
            display: none;
        }
        
        .footer-actions {
            display: flex;
            gap: 0.5rem;
            flex-shrink: 0;
        }
        
        .build-btn {
            background: linear-gradient(135deg, #51cf66 0%, #339af0 100%);
            border: none;
            padding: 0.75rem 1.5rem;
            border-radius: 12px;
            font-weight: 500;
            display: flex;
            align-items: center;
            transition: all 0.3s ease;
        }
        
        .build-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(81, 207, 102, 0.4);
        }
        
        /* Animations */
        @keyframes fadeIn {
            from {
                opacity: 0;
                transform: translateY(10px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        
        .fade-in {
            animation: fadeIn 0.3s ease;
        }
        
        /* Scrollbar Styling */
        .chat-messages::-webkit-scrollbar,
        .requirements-sidebar::-webkit-scrollbar {
            width: 8px;
        }
        
        .chat-messages::-webkit-scrollbar-track,
        .requirements-sidebar::-webkit-scrollbar-track {
            background: #f1f3f5;
            border-radius: 10px;
        }
        
        .chat-messages::-webkit-scrollbar-thumb,
        .requirements-sidebar::-webkit-scrollbar-thumb {
            background: #dee2e6;
            border-radius: 10px;
        }
        
        .chat-messages::-webkit-scrollbar-thumb:hover,
        .requirements-sidebar::-webkit-scrollbar-thumb:hover {
            background: #ced4da;
        }
        
        /* Loading State */
        .typing-indicator {
            display: flex;
            gap: 4px;
            padding: 1rem;
        }
        
        .typing-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #667eea;
            animation: typing 1.4s infinite ease-in-out;
        }
        
        .typing-dot:nth-child(1) { animation-delay: -0.32s; }
        .typing-dot:nth-child(2) { animation-delay: -0.16s; }
        
        @keyframes typing {
            0%, 80%, 100% {
                transform: scale(1);
                opacity: 0.5;
            }
            40% {
                transform: scale(1.3);
                opacity: 1;
            }
        }

        /* Workflow Commands Collapsible Container */
        .workflow-commands-container {
            margin: 1rem 0;
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 8px;
            overflow: hidden;
            background: rgba(0, 0, 0, 0.2);
            transition: all 0.3s ease;
            max-width: 100%;  /* CRITICAL: Constrain to parent width */
            width: 100%;      /* CRITICAL: Take full available width */
        }

        .workflow-commands-container:hover {
            border-color: rgba(103, 126, 234, 0.3);
            box-shadow: 0 2px 8px rgba(103, 126, 234, 0.1);
        }

        /* Toggle Button */
        .workflow-commands-toggle {
            width: 100%;
            padding: 0.75rem 1rem;
            border: none;
            background: linear-gradient(135deg, rgba(103, 126, 234, 0.1) 0%, rgba(118, 75, 162, 0.1) 100%);
            color: #fff;
            display: flex;
            align-items: center;
            justify-content: space-between;
            cursor: pointer;
            transition: all 0.2s ease;
            font-weight: 500;
        }

        .workflow-commands-toggle:hover {
            background: linear-gradient(135deg, rgba(103, 126, 234, 0.2) 0%, rgba(118, 75, 162, 0.2) 100%);
        }

        .workflow-commands-toggle.expanded {
            background: linear-gradient(135deg, rgba(103, 126, 234, 0.15) 0%, rgba(118, 75, 162, 0.15) 100%);
        }

        .toggle-header {
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .toggle-title {
            font-size: 0.9rem;
        }

        .toggle-icon {
            transition: transform 0.3s ease;
            font-size: 1rem;
        }

        .workflow-commands-toggle.expanded .toggle-icon {
            transform: rotate(180deg);
        }

        /* Collapsible Content */
        .workflow-commands-content {
            background: #1e1e1e;
            border-top: 1px solid rgba(255, 255, 255, 0.05);
            max-height: 0;
            overflow: hidden;
            transition: max-height 0.3s ease;
            width: 100%;           /* CRITICAL: Constrain width */
            max-width: 100%;       /* CRITICAL: Constrain width */
        }

        .workflow-commands-content.show {
            max-height: 600px;
            overflow-y: auto;
            overflow-x: auto;      /* CRITICAL: Allow horizontal scroll for wide code */
        }

        /* Commands Toolbar */
        .commands-toolbar {
            padding: 0.5rem 1rem;
            background: rgba(0, 0, 0, 0.3);
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            display: flex;
            justify-content: flex-end;
        }

        .commands-toolbar .copy-btn {
            padding: 0.25rem 0.75rem;
            font-size: 0.85rem;
            border-color: rgba(255, 255, 255, 0.2);
            color: rgba(255, 255, 255, 0.7);
            transition: all 0.2s ease;
        }

        .commands-toolbar .copy-btn:hover {
            background: rgba(255, 255, 255, 0.1);
            border-color: rgba(255, 255, 255, 0.3);
            color: #fff;
        }

        /* Code Block Styling */
        .workflow-commands-content pre {
            margin: 0;
            padding: 1rem;
            background: transparent;
            overflow-x: auto;
            max-width: 100%;
            width: 100%;
            box-sizing: border-box;
            
            /* CRITICAL: Force pre to respect parent width */
            word-wrap: break-word;      /* ADD THIS */
            white-space: pre-wrap;      /* CHANGE from 'pre' to 'pre-wrap' */
            overflow-wrap: break-word;  /* ADD THIS */
            min-width: 0;               /* ADD THIS - critical for flex/grid parents */
        }

        .workflow-commands-content code {
            font-family: 'SF Mono', 'Monaco', 'Inconsolata', 'Fira Code', 'Consolas', monospace;
            font-size: 0.85rem;
            line-height: 1.6;
            color: #abb2bf;
            display: block;
            max-width: 100%;
        }

        /* Scrollbar for vertical scrolling in content area */
        .workflow-commands-content::-webkit-scrollbar {
            height: 8px;
            width: 8px;
        }

        .workflow-commands-content::-webkit-scrollbar-track {
            background: rgba(0, 0, 0, 0.2);
        }

        .workflow-commands-content::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.2);
            border-radius: 4px;
        }

        .workflow-commands-content::-webkit-scrollbar-thumb:hover {
            background: rgba(255, 255, 255, 0.3);
        }

        /* Horizontal scrollbar for pre tag */
        .workflow-commands-content pre::-webkit-scrollbar {
            height: 8px;
        }

        .workflow-commands-content pre::-webkit-scrollbar-track {
            background: rgba(0, 0, 0, 0.2);
        }

        .workflow-commands-content pre::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.2);
            border-radius: 4px;
        }

        .workflow-commands-content pre::-webkit-scrollbar-thumb:hover {
            background: rgba(255, 255, 255, 0.3);
        }

        /* Responsive adjustments */
        @media (max-width: 768px) {
            .workflow-commands-toggle {
                padding: 0.6rem 0.75rem;
                font-size: 0.85rem;
            }
            
            .workflow-commands-content code {
                font-size: 0.75rem;
            }
            
            .commands-toolbar {
                padding: 0.4rem 0.75rem;
            }
        }

        /* Workflow Plan Container */
        .workflow-plan-container {
            background: linear-gradient(135deg, rgba(13, 110, 253, 0.1) 0%, rgba(13, 110, 253, 0.05) 100%);
            border: 1px solid rgba(13, 110, 253, 0.3);
            border-left: 4px solid #0d6efd;
            border-radius: 8px;
            margin: 0.75rem 0;
            overflow: hidden;
        }

        .workflow-plan-header {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.75rem 1rem;
            background: rgba(13, 110, 253, 0.15);
            border-bottom: 1px solid rgba(13, 110, 253, 0.2);
            color: #6ea8fe;
            font-weight: 600;
            font-size: 0.9rem;
        }

        .workflow-plan-header i {
            font-size: 1.1rem;
        }

        .workflow-plan-content {
            padding: 1rem;
            color: rgba(255, 255, 255, 0.9);
            font-size: 0.9rem;
            line-height: 1.7;
        }

        .workflow-plan-content .plan-step {
            display: flex;
            align-items: flex-start;
            margin-bottom: 0.6rem;
            padding-left: 0.25rem;
        }

        .workflow-plan-content .plan-step:last-child {
            margin-bottom: 0;
        }

        .workflow-plan-content .step-number {
            color: #0d6efd;
            font-weight: 600;
            min-width: 1.5rem;
            margin-right: 0.5rem;
        }

        .workflow-plan-content .step-text {
            flex: 1;
        }

        .workflow-plan-content .branch-item {
            margin-left: 2rem;
            margin-top: 0.3rem;
            padding-left: 0.75rem;
            border-left: 2px solid rgba(13, 110, 253, 0.3);
            color: rgba(255, 255, 255, 0.8);
            font-size: 0.85rem;
        }

        /* Animation for smooth expand/collapse */
        @keyframes slideDown {
            from {
                opacity: 0;
                transform: translateY(-10px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }

        .workflow-commands-content.show {
            animation: slideDown 0.3s ease;
        }
        
        /* Workflow Building Animation Overlay */
        .workflow-building-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: linear-gradient(135deg, rgba(102, 126, 234, 0.95) 0%, rgba(118, 75, 162, 0.95) 100%);
            z-index: 10000;
            display: flex;
            align-items: center;
            justify-content: center;
            opacity: 0;
            transition: opacity 0.5s ease;
        }
        
        .workflow-building-overlay.active {
            opacity: 1;
        }
        
        .workflow-building-overlay.fade-out {
            opacity: 0;
        }
        
        .building-animation-container {
            text-align: center;
            color: white;
            max-width: 600px;
        }
        
        .building-header {
            margin-bottom: 3rem;
            transition: all 0.5s ease;
        }
        
        .building-header.complete .building-icon {
            animation: successPulse 0.6s ease;
        }
        
        .building-icon {
            margin: 0 auto 1.5rem;
            width: 80px;
            height: 80px;
            background: rgba(255, 255, 255, 0.2);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            animation: float 3s ease-in-out infinite;
        }
        
        .building-icon svg {
            filter: drop-shadow(0 4px 8px rgba(0,0,0,0.2));
            animation: rotate3d 4s ease-in-out infinite;
        }
        
        .building-header h3 {
            font-size: 2rem;
            font-weight: 600;
            margin-bottom: 0.75rem;
            text-shadow: 0 2px 10px rgba(0,0,0,0.2);
        }
        
        .building-status {
            font-size: 1.1rem;
            opacity: 0.9;
            min-height: 1.5rem;
            animation: fadeInText 0.5s ease;
        }
        
        .building-visualization {
            position: relative;
            margin: 3rem auto;
            height: 300px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        .node-container {
            position: absolute;
            width: 400px;
            height: 200px;
        }
        
        .animated-node {
            position: absolute;
            width: 50px;
            height: 50px;
            background: white;
            border-radius: 12px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.2);
            opacity: 0;
            transform: scale(0);
        }
        
        .animated-node.node-1 {
            left: 50px;
            top: 30px;
            animation: nodeAppear 0.5s ease 0.3s forwards;
        }
        
        .animated-node.node-2 {
            left: 175px;
            top: 30px;
            animation: nodeAppear 0.5s ease 0.6s forwards;
        }
        
        .animated-node.node-3 {
            left: 275px;
            top: 30px;
            animation: nodeAppear 0.5s ease 1.2s forwards;
        }
        
        .animated-node.node-4 {
            left: 175px;
            top: 120px;
            animation: nodeAppear 0.5s ease 0.9s forwards;
        }
        
        .animated-node.node-5 {
            left: 275px;
            top: 120px;
            animation: nodeAppear 0.5s ease 1.5s forwards;
        }
        
        .connection-lines {
            position: absolute;
            width: 400px;
            height: 300px;
        }
        
        .animated-connection {
            stroke-dasharray: 300;
            stroke-dashoffset: 300;
            opacity: 0.8;
            filter: drop-shadow(0 0 5px rgba(255,255,255,0.5));
        }
        
        .animated-connection.conn-1 {
            animation: drawLine 0.5s ease 0.8s forwards;
        }
        
        .animated-connection.conn-2 {
            animation: drawLine 0.5s ease 1.4s forwards;
        }
        
        .animated-connection.conn-3 {
            animation: drawLine 0.5s ease 1.1s forwards;
        }
        
        .animated-connection.conn-4 {
            animation: drawLine 0.5s ease 1.7s forwards;
        }
        
        .building-progress {
            margin-top: 3rem;
        }
        
        .progress-bar-animated {
            height: 8px;
            background: rgba(255,255,255,0.2);
            border-radius: 10px;
            overflow: hidden;
            margin-bottom: 2rem;
        }
        
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #51cf66 0%, #339af0 100%);
            border-radius: 10px;
            width: 0%;
            animation: progressFill 3.5s ease forwards;
            box-shadow: 0 0 10px rgba(81, 207, 102, 0.5);
        }
        
        .building-steps {
            display: flex;
            justify-content: space-around;
            gap: 1rem;
        }
        
        .building-steps .step {
            flex: 1;
            text-align: center;
            opacity: 0.4;
            transition: all 0.3s ease;
            font-size: 0.9rem;
        }
        
        .building-steps .step.active {
            opacity: 1;
            transform: scale(1.05);
        }
        
        .building-steps .step i {
            display: block;
            margin-bottom: 0.5rem;
            font-size: 1.5rem;
        }
        
        /* Animations */
        @keyframes float {
            0%, 100% {
                transform: translateY(0);
            }
            50% {
                transform: translateY(-15px);
            }
        }
        
        @keyframes rotate3d {
            0%, 100% {
                transform: perspective(400px) rotateY(0deg);
            }
            50% {
                transform: perspective(400px) rotateY(180deg);
            }
        }
        
        @keyframes nodeAppear {
            0% {
                opacity: 0;
                transform: scale(0) rotate(-180deg);
            }
            60% {
                transform: scale(1.1) rotate(10deg);
            }
            100% {
                opacity: 1;
                transform: scale(1) rotate(0deg);
            }
        }
        
        @keyframes drawLine {
            to {
                stroke-dashoffset: 0;
            }
        }
        
        @keyframes progressFill {
            to {
                width: 100%;
            }
        }
        
        @keyframes fadeInText {
            from {
                opacity: 0;
                transform: translateY(5px);
            }
            to {
                opacity: 0.9;
                transform: translateY(0);
            }
        }
        
        @keyframes successPulse {
            0%, 100% {
                transform: scale(1);
            }
            50% {
                transform: scale(1.1);
            }
        }



        /* ====================================
        CSS FOR ALTERNATIVE ANIMATION V2
        Add this to the injectStyles() function
        ==================================== */

        /* Alternative AI Working Animation */
        .workflow-building-overlay-v2 {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: linear-gradient(135deg, 
            rgba(1, 87, 155, 0.85) 0%, 
            rgba(2, 119, 189, 0.85) 50%,
            rgba(1, 87, 155, 0.85) 100%);
            background-size: 200% 200%;
            animation: gradientShift 8s ease infinite;
            z-index: 10000;
            display: flex;
            align-items: center;
            justify-content: center;
            opacity: 0;
            transition: opacity 0.5s ease;
            overflow: hidden;
        }

        .workflow-building-overlay-v2.active {
            opacity: 1;
        }

        .workflow-building-overlay-v2.fade-out {
            opacity: 0;
        }

        .ai-working-container {
            position: relative;
            text-align: center;
            color: white;
            z-index: 2;
        }

        /* AI Brain Visualization */
        .ai-brain-visualization {
            margin-bottom: 3rem;
        }

        .brain-core {
            position: relative;
            display: inline-block;
            filter: drop-shadow(0 0 40px rgba(138, 180, 248, 0.9));
        }

        .brain-core.complete {
            animation: successFlash 0.5s ease;
        }

        .brain-center {
            fill: rgba(138, 180, 248, 0.3);
            stroke: rgba(138, 180, 248, 0.9);
            stroke-width: 2;
            animation: corePulse 2s ease-in-out infinite;
        }

        .pulse-ring {
            fill: none;
            stroke: rgba(138, 180, 248, 0.4);
            stroke-width: 2;
            opacity: 0;
        }

        .pulse-ring.ring-1 {
            animation: ringPulse 3s ease-in-out infinite;
        }

        .pulse-ring.ring-2 {
            animation: ringPulse 3s ease-in-out 1s infinite;
        }

        .pulse-ring.ring-3 {
            animation: ringPulse 3s ease-in-out 2s infinite;
        }

        .neural-path {
            fill: none;
            stroke: rgba(138, 180, 248, 0.6);
            stroke-width: 2;
            stroke-linecap: round;
        }

        .neural-path.path-1 {
            animation: pathFlow 2s ease-in-out infinite;
        }

        .neural-path.path-2 {
            animation: pathFlow 2s ease-in-out 0.5s infinite;
        }

        .neural-path.path-3 {
            animation: pathFlow 2s ease-in-out 1s infinite;
        }

        .neural-path.path-4 {
            animation: pathFlow 2s ease-in-out 1.5s infinite;
        }

        .neural-node {
            fill: rgba(138, 180, 248, 0.9);
            stroke: rgba(255, 255, 255, 0.8);
            stroke-width: 1;
        }

        .neural-node.node-1 {
            animation: nodeBlink 2s ease-in-out infinite;
        }

        .neural-node.node-2 {
            animation: nodeBlink 2s ease-in-out 0.5s infinite;
        }

        .neural-node.node-3 {
            animation: nodeBlink 2s ease-in-out 1s infinite;
        }

        .neural-node.node-4 {
            animation: nodeBlink 2s ease-in-out 1.5s infinite;
        }

        /* Status Text */
        .ai-status {
            margin-top: 2rem;
        }

        .ai-title {
            font-size: 2.5rem;
            font-weight: 500;
            letter-spacing: 0.1em;
            margin-bottom: 1rem;
            text-shadow: 0 0 20px rgba(138, 180, 248, 0.5);
            animation: titleGlow 2s ease-in-out infinite;
        }

        .ai-message {
            font-size: 1.3rem;
            opacity: 1;
            transition: opacity 0.3s ease;
            color: #ffffff;
            min-height: 1.5rem;
            font-weight: 600;
            letter-spacing: 0.03em;
            text-shadow: 0 2px 8px rgba(0, 0, 0, 0.5);
            margin-top: 1rem;
        }

        /* Particle Field Background */
        .particle-field {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            z-index: 1;
            pointer-events: none;
        }

        .particle {
            position: absolute;
            width: 3px;
            height: 3px;
            background: rgba(138, 180, 248, 0.6);
            border-radius: 50%;
            box-shadow: 0 0 10px rgba(138, 180, 248, 0.8);
        }

        /* Random particle positions and animations */
        .particle-0 { left: 10%; top: 20%; animation: particleFloat 8s ease-in-out infinite; }
        .particle-1 { left: 85%; top: 15%; animation: particleFloat 7s ease-in-out 0.5s infinite; }
        .particle-2 { left: 20%; top: 80%; animation: particleFloat 9s ease-in-out 1s infinite; }
        .particle-3 { left: 75%; top: 70%; animation: particleFloat 6s ease-in-out 1.5s infinite; }
        .particle-4 { left: 50%; top: 10%; animation: particleFloat 10s ease-in-out 2s infinite; }
        .particle-5 { left: 15%; top: 50%; animation: particleFloat 7.5s ease-in-out 2.5s infinite; }
        .particle-6 { left: 90%; top: 45%; animation: particleFloat 8.5s ease-in-out 3s infinite; }
        .particle-7 { left: 30%; top: 30%; animation: particleFloat 9.5s ease-in-out 0.3s infinite; }
        .particle-8 { left: 65%; top: 85%; animation: particleFloat 7s ease-in-out 0.8s infinite; }
        .particle-9 { left: 45%; top: 65%; animation: particleFloat 11s ease-in-out 1.2s infinite; }
        .particle-10 { left: 8%; top: 40%; animation: particleFloat 6.5s ease-in-out 1.8s infinite; }
        .particle-11 { left: 92%; top: 60%; animation: particleFloat 8s ease-in-out 2.2s infinite; }
        .particle-12 { left: 25%; top: 90%; animation: particleFloat 9s ease-in-out 0.4s infinite; }
        .particle-13 { left: 70%; top: 25%; animation: particleFloat 7.5s ease-in-out 1.6s infinite; }
        .particle-14 { left: 55%; top: 55%; animation: particleFloat 10.5s ease-in-out 2.8s infinite; }
        .particle-15 { left: 12%; top: 70%; animation: particleFloat 8.5s ease-in-out 0.6s infinite; }
        .particle-16 { left: 88%; top: 30%; animation: particleFloat 7s ease-in-out 3.2s infinite; }
        .particle-17 { left: 40%; top: 15%; animation: particleFloat 9.5s ease-in-out 1.4s infinite; }
        .particle-18 { left: 60%; top: 75%; animation: particleFloat 11.5s ease-in-out 2.4s infinite; }
        .particle-19 { left: 35%; top: 45%; animation: particleFloat 6.8s ease-in-out 0.9s infinite; }

        /* Animations */
        @keyframes gradientShift {
            0%, 100% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
        }

        @keyframes corePulse {
            0%, 100% {
                r: 25;
                fill-opacity: 0.3;
            }
            50% {
                r: 27;
                fill-opacity: 0.5;
            }
        }

        @keyframes ringPulse {
            0% {
                r: 40;
                opacity: 0;
            }
            50% {
                opacity: 0.6;
            }
            100% {
                r: 60;
                opacity: 0;
            }
        }

        @keyframes pathFlow {
            0%, 100% {
                stroke-opacity: 0.3;
                stroke-width: 2;
            }
            50% {
                stroke-opacity: 1;
                stroke-width: 3;
            }
        }

        @keyframes nodeBlink {
            0%, 100% {
                r: 4;
                fill-opacity: 0.6;
            }
            50% {
                r: 5;
                fill-opacity: 1;
            }
        }

        @keyframes titleGlow {
            0%, 100% {
                text-shadow: 0 0 20px rgba(138, 180, 248, 0.5);
            }
            50% {
                text-shadow: 0 0 30px rgba(138, 180, 248, 0.8);
            }
        }

        @keyframes particleFloat {
            0%, 100% {
                transform: translate(0, 0);
                opacity: 0.3;
            }
            25% {
                transform: translate(20px, -30px);
                opacity: 0.8;
            }
            50% {
                transform: translate(-15px, -60px);
                opacity: 0.5;
            }
            75% {
                transform: translate(30px, -40px);
                opacity: 0.9;
            }
        }

        @keyframes successFlash {
            0%, 100% {
                filter: drop-shadow(0 0 30px rgba(138, 180, 248, 0.6));
            }
            50% {
                filter: drop-shadow(0 0 50px rgba(76, 209, 55, 0.8));
            }
        }


        /* Alternative AI Working Animation V2 */
.workflow-building-overlay-v2 {
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background: linear-gradient(135deg, 
    rgba(1, 87, 155, 0.85) 0%, 
    rgba(2, 119, 189, 0.85) 50%,
    rgba(1, 87, 155, 0.85) 100%);
    background-size: 200% 200%;
    animation: gradientShift 8s ease infinite;
    z-index: 10000;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    transition: opacity 0.5s ease;
    overflow: hidden;
}

.workflow-building-overlay-v2.active {
    opacity: 1;
}

.workflow-building-overlay-v2.fade-out {
    opacity: 0;
}

.ai-working-container {
    position: relative;
    text-align: center;
    color: white;
    z-index: 2;
}

/* AI Brain Visualization */
.ai-brain-visualization {
    margin-bottom: 3rem;
}

.brain-core {
    position: relative;
    display: inline-block;
    filter: drop-shadow(0 0 30px rgba(138, 180, 248, 0.6));
}

.brain-core.complete {
    animation: successFlash 0.5s ease;
}

.brain-center {
    fill: rgba(138, 180, 248, 0.3);
    stroke: rgba(138, 180, 248, 0.9);
    stroke-width: 2;
    animation: corePulse 2s ease-in-out infinite;
}

.pulse-ring {
    fill: none;
    stroke: rgba(138, 180, 248, 0.4);
    stroke-width: 2;
    opacity: 0;
}

.pulse-ring.ring-1 {
    animation: ringPulse 3s ease-in-out infinite;
}

.pulse-ring.ring-2 {
    animation: ringPulse 3s ease-in-out 1s infinite;
}

.pulse-ring.ring-3 {
    animation: ringPulse 3s ease-in-out 2s infinite;
}

.neural-path {
    fill: none;
    stroke: rgba(138, 180, 248, 0.6);
    stroke-width: 2;
    stroke-linecap: round;
}

.neural-path.path-1 {
    animation: pathFlow 2s ease-in-out infinite;
}

.neural-path.path-2 {
    animation: pathFlow 2s ease-in-out 0.5s infinite;
}

.neural-path.path-3 {
    animation: pathFlow 2s ease-in-out 1s infinite;
}

.neural-path.path-4 {
    animation: pathFlow 2s ease-in-out 1.5s infinite;
}

.neural-node {
    fill: rgba(138, 180, 248, 0.9);
    stroke: rgba(255, 255, 255, 0.8);
    stroke-width: 1;
}

.neural-node.node-1 {
    animation: nodeBlink 2s ease-in-out infinite;
}

.neural-node.node-2 {
    animation: nodeBlink 2s ease-in-out 0.5s infinite;
}

.neural-node.node-3 {
    animation: nodeBlink 2s ease-in-out 1s infinite;
}

.neural-node.node-4 {
    animation: nodeBlink 2s ease-in-out 1.5s infinite;
}

/* Status Text */
.ai-status {
    margin-top: 2rem;
}

.ai-title {
    font-size: 2.5rem;
    font-weight: 300;
    letter-spacing: 0.1em;
    margin-bottom: 1rem;
    text-shadow: 0 0 20px rgba(138, 180, 248, 0.5);
    animation: titleGlow 2s ease-in-out infinite;
}

.ai-message {
    font-size: 1.1rem;
    opacity: 1;
    transition: opacity 0.3s ease;
    color: rgba(255, 255, 255, 0.9);
    min-height: 1.5rem;
    font-weight: 300;
    letter-spacing: 0.02em;
}

/* Particle Field Background */
.particle-field {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    z-index: 1;
    pointer-events: none;
}

.particle {
    position: absolute;
    width: 3px;
    height: 3px;
    background: rgba(138, 180, 248, 0.6);
    border-radius: 50%;
    box-shadow: 0 0 10px rgba(138, 180, 248, 0.8);
}

/* Random particle positions and animations */
.particle-0 { left: 10%; top: 20%; animation: particleFloat 8s ease-in-out infinite; }
.particle-1 { left: 85%; top: 15%; animation: particleFloat 7s ease-in-out 0.5s infinite; }
.particle-2 { left: 20%; top: 80%; animation: particleFloat 9s ease-in-out 1s infinite; }
.particle-3 { left: 75%; top: 70%; animation: particleFloat 6s ease-in-out 1.5s infinite; }
.particle-4 { left: 50%; top: 10%; animation: particleFloat 10s ease-in-out 2s infinite; }
.particle-5 { left: 15%; top: 50%; animation: particleFloat 7.5s ease-in-out 2.5s infinite; }
.particle-6 { left: 90%; top: 45%; animation: particleFloat 8.5s ease-in-out 3s infinite; }
.particle-7 { left: 30%; top: 30%; animation: particleFloat 9.5s ease-in-out 0.3s infinite; }
.particle-8 { left: 65%; top: 85%; animation: particleFloat 7s ease-in-out 0.8s infinite; }
.particle-9 { left: 45%; top: 65%; animation: particleFloat 11s ease-in-out 1.2s infinite; }
.particle-10 { left: 8%; top: 40%; animation: particleFloat 6.5s ease-in-out 1.8s infinite; }
.particle-11 { left: 92%; top: 60%; animation: particleFloat 8s ease-in-out 2.2s infinite; }
.particle-12 { left: 25%; top: 90%; animation: particleFloat 9s ease-in-out 0.4s infinite; }
.particle-13 { left: 70%; top: 25%; animation: particleFloat 7.5s ease-in-out 1.6s infinite; }
.particle-14 { left: 55%; top: 55%; animation: particleFloat 10.5s ease-in-out 2.8s infinite; }
.particle-15 { left: 12%; top: 70%; animation: particleFloat 8.5s ease-in-out 0.6s infinite; }
.particle-16 { left: 88%; top: 30%; animation: particleFloat 7s ease-in-out 3.2s infinite; }
.particle-17 { left: 40%; top: 15%; animation: particleFloat 9.5s ease-in-out 1.4s infinite; }
.particle-18 { left: 60%; top: 75%; animation: particleFloat 11.5s ease-in-out 2.4s infinite; }
.particle-19 { left: 35%; top: 45%; animation: particleFloat 6.8s ease-in-out 0.9s infinite; }

/* Node entrance animation for canvas nodes */
.workflow-node.ai-building {
    animation: nodeEntrance 1.4s cubic-bezier(0.34, 1.56, 0.64, 1);
    transform-origin: center;
}

@keyframes nodeEntrance {
    0% {
        opacity: 0;
        transform: scale(0) rotate(-180deg);
    }
    60% {
        transform: scale(1.15) rotate(10deg);
    }
    100% {
        opacity: 1;
        transform: scale(1) rotate(0deg);
    }
}

/* Connection drawing animation */
.jtk-connector.ai-building {
    animation: connectionDraw 1.3s ease-out;
}

@keyframes connectionDraw {
    0% {
        stroke-dasharray: 1000;
        stroke-dashoffset: 1000;
        opacity: 0;
    }
    100% {
        stroke-dasharray: 1000;
        stroke-dashoffset: 0;
        opacity: 1;
    }
}

/* Base animations */
@keyframes gradientShift {
    0%, 100% { background-position: 0% 50%; }
    50% { background-position: 100% 50%; }
}

@keyframes corePulse {
    0%, 100% {
        r: 25;
        fill-opacity: 0.3;
    }
    50% {
        r: 27;
        fill-opacity: 0.5;
    }
}

@keyframes ringPulse {
    0% {
        r: 40;
        opacity: 0;
    }
    50% {
        opacity: 0.6;
    }
    100% {
        r: 60;
        opacity: 0;
    }
}

@keyframes pathFlow {
    0%, 100% {
        stroke-opacity: 0.3;
        stroke-width: 2;
    }
    50% {
        stroke-opacity: 1;
        stroke-width: 3;
    }
}

@keyframes nodeBlink {
    0%, 100% {
        r: 4;
        fill-opacity: 0.6;
    }
    50% {
        r: 5;
        fill-opacity: 1;
    }
}

@keyframes titleGlow {
    0%, 100% {
        text-shadow: 0 0 20px rgba(138, 180, 248, 0.5);
    }
    50% {
        text-shadow: 0 0 30px rgba(138, 180, 248, 0.8);
    }
}

@keyframes particleFloat {
    0%, 100% {
        transform: translate(0, 0);
        opacity: 0.3;
    }
    25% {
        transform: translate(20px, -30px);
        opacity: 0.8;
    }
    50% {
        transform: translate(-15px, -60px);
        opacity: 0.5;
    }
    75% {
        transform: translate(30px, -40px);
        opacity: 0.9;
    }
}

@keyframes successFlash {
    0%, 100% {
        filter: drop-shadow(0 0 30px rgba(138, 180, 248, 0.6));
    }
    50% {
        filter: drop-shadow(0 0 50px rgba(76, 209, 55, 0.8));
    }
}
        </style>`;
        
        document.head.insertAdjacentHTML('beforeend', styles);
    }

    getWorkflowState() {
        /**
         * Capture the current workflow state from the canvas
         * This includes all nodes, connections, and configurations
         */
        const nodes = [];
        const connections = [];
        
        // Collect all nodes from the canvas
        document.querySelectorAll('.workflow-node').forEach(node => {
            nodes.push({
                id: node.id,
                type: node.getAttribute('data-type'),
                label: node.querySelector('.node-content').textContent.trim(),
                position: {
                    left: node.style.left,
                    top: node.style.top
                },
                config: nodeConfigs.get(node.id) || {},
                isStart: node === startNode
            });
        });
        
        // Collect all connections
        if (typeof jsPlumbInstance !== 'undefined') {
            jsPlumbInstance.getAllConnections().forEach(conn => {
                const sourceEndpoint = conn.endpoints[0];
                const targetEndpoint = conn.endpoints[1];
                const sourceAnchor = sourceEndpoint.anchor.type || "Right";
                const targetAnchor = targetEndpoint.anchor.type || "Left";
                
                connections.push({
                    source: conn.source.id,
                    target: conn.target.id,
                    type: conn.getData().type || 'pass',
                    sourceAnchor: sourceAnchor,
                    targetAnchor: targetAnchor
                });
            });
        }
        
        return {
            name: currentWorkflowName || null,
            workflow_name: currentWorkflowName || null,  // Include both for compatibility
            nodes: nodes,
            connections: connections
        };
    }


    async open() {
        if (this.isOpen) return;
        
        const modal = bootstrap.Modal.getOrCreateInstance(
            document.getElementById('workflowBuilderModal')
        );
        modal.show();
        this.isOpen = true;
        
        const workflowState = this.getWorkflowState();
        const hasWorkflow = workflowState.nodes && workflowState.nodes.length > 0;
        
        console.log('Opening AI Builder:', {
            sessionId: this.sessionId,
            hasWorkflow,
            nodeCount: workflowState.nodes?.length || 0
        });
        
        const messagesContainer = document.getElementById('builderMessages');
        messagesContainer.innerHTML = '';
        
        // Fetch conversation history from backend (includes greeting)
        try {
            const response = await fetch(`/api/workflow/builder/history?session_id=${this.sessionId}`);
            const data = await response.json();
            
            if (data.status === 'success' && data.history && data.history.length > 0) {
                console.log(`Restoring ${data.history.length} messages from backend`);
                
                data.history.forEach(msg => {
                    this.addMessage(msg.content, msg.role);
                });
                
                if (hasWorkflow) {
                    this.updatePhase('refinement');
                }
            } else {
                // No session yet - show greeting (session will be created on first message)
                this.showInitialGreeting(hasWorkflow, workflowState);
            }
            
        } catch (error) {
            console.error('Failed to load conversation history:', error);
            this.showInitialGreeting(hasWorkflow, workflowState);
        }
        
        // Focus input
        setTimeout(() => {
            const input = document.getElementById('builderInput');
            if (input) input.focus();
        }, 300);
    }

    showInitialGreeting(hasWorkflow, workflowState) {
        if (hasWorkflow) {
            const nodeTypes = {};
            workflowState.nodes.forEach(node => {
                nodeTypes[node.type] = (nodeTypes[node.type] || 0) + 1;
            });
            const nodeTypeSummary = Object.entries(nodeTypes)
                .map(([type, count]) => `${count} ${type}`)
                .join(', ');
            const workflowName = workflowState.name || 'your workflow';
            
            this.addMessage(
                `Hi! I can see you have an existing workflow <strong>"${workflowName}"</strong> ` +
                `with <strong>${workflowState.nodes.length} nodes</strong> (${nodeTypeSummary}).\n\n` +
                `What would you like to change or improve?`,
                'assistant'
            );
            this.updatePhase('refinement');
        } else {
            this.addMessage(
                `Hi! I'm here to help you build a workflow that automates your business process. ` +
                `Let's start by understanding what you'd like to automate.\n\n` +
                `What process or task are you looking to streamline?`,
                'assistant'
            );
            this.updatePhase('discovery');
        }
    }

    open_deprecated() {
        if (this.isOpen) return;
        
        const modal = bootstrap.Modal.getOrCreateInstance(
            document.getElementById('workflowBuilderModal')
        );
        modal.show();
        this.isOpen = true;
        
        // Get current workflow state
        const workflowState = this.getWorkflowState();
        const hasWorkflow = workflowState.nodes && workflowState.nodes.length > 0;
        
        console.log('Opening AI Builder:', {
            hasWorkflow,
            nodeCount: workflowState.nodes.length,
            currentPhase: this.currentPhase
        });
        
        // *** Check if we should preserve conversation ***
        const messagesContainer = document.getElementById('builderMessages');
        const messageCount = messagesContainer.children.length;
        
        // Preserve conversation if:
        // 1. There are multiple messages (more than just the initial greeting)
        // 2. AND there's actually a workflow on the canvas
        const shouldPreserve = messageCount > 1 && hasWorkflow;
        
        if (shouldPreserve) {
            console.log('Preserving conversation - multiple messages with workflow present');
            
            // Update phase if needed
            if (this.currentPhase !== 'refinement') {
                this.updatePhase('refinement');
            }
            
            // Just focus input and return - don't touch messages
            setTimeout(() => {
                const input = document.getElementById('builderInput');
                if (input) input.focus();
            }, 500);
            return;
        }
        
        // *** ALWAYS CLEAR if not preserving ***
        messagesContainer.innerHTML = '';
        
        // Show appropriate greeting based on workflow state
        if (hasWorkflow) {
            // Pre-existing workflow - show refine greeting
            const nodeTypes = {};
            workflowState.nodes.forEach(node => {
                nodeTypes[node.type] = (nodeTypes[node.type] || 0) + 1;
            });
            
            const nodeTypeSummary = Object.entries(nodeTypes)
                .map(([type, count]) => `${count} ${type}`)
                .join(', ');
            
            const workflowName = workflowState.name || 'your workflow';
            
            this.addMessage(
                `Hi! I can see you have an existing workflow <strong>"${workflowName}"</strong> ` +
                `with <strong>${workflowState.nodes.length} nodes</strong> ` +
                `(${nodeTypeSummary}).\n\n` +
                `What would you like to change or improve?`,
                'assistant'
            );
            
            this.updatePhase('refinement');
        } else {
            // No workflow - show build greeting
            this.addMessage(
                `Hi! I'm here to help you build a workflow that automates your business process. ` +
                `Let's start by understanding what you'd like to automate.\n\n` +
                `What process or task are you looking to streamline?`,
                'assistant'
            );
            
            this.updatePhase('discovery');
        }
        
        // Focus input
        setTimeout(() => {
            const input = document.getElementById('builderInput');
            if (input) input.focus();
        }, 500);
    }

    showRefineModeInfo() {
        /**
         * Display helpful information about refine mode capabilities
         */
        const workflowState = this.getWorkflowState();
        
        const infoMessage = `
            <div class="alert alert-info" role="alert">
                <h6><i class="bi bi-info-circle"></i> Refine Mode Active</h6>
                <p class="mb-1">You're editing an existing workflow. I can help you:</p>
                <ul class="mb-0">
                    <li>Add new nodes between existing steps</li>
                    <li>Delete or modify current nodes</li>
                    <li>Update node configurations</li>
                    <li>Reorganize connections and flow</li>
                    <li>Optimize the workflow structure</li>
                </ul>
            </div>
        `;
        
        // Could display this in the sidebar or as a notification
        return infoMessage;
    }

    buildWorkflowContext() {
        // Get all nodes from the workflow designer
        const nodes = Array.from(document.querySelectorAll('.workflow-node')).map(node => {
            const config = nodeConfigs.get(node.id) || {};
            return {
                id: node.id,
                type: node.getAttribute('data-type'),
                label: node.querySelector('.node-content')?.textContent.trim() || '',
                isStart: node === startNode,
                config: config,
                position: {
                    x: parseInt(node.style.left) || 0,
                    y: parseInt(node.style.top) || 0
                }
            };
        });
        
        // Get all connections
        const connections = jsPlumbInstance.getAllConnections().map(conn => ({
            from: conn.sourceId,
            to: conn.targetId,
            type: conn.getParameter('type') || 'pass'
        }));
        
        return {
            workflowName: currentWorkflowName || 'Untitled',
            nodeCount: nodes.length,
            hasStartNode: startNode !== null,
            nodes: nodes,
            connections: connections
        };
    }

    getWorkflowStats() {
        /**
         * Get statistics about the current workflow
         */
        const state = this.getWorkflowState();
        
        const stats = {
            totalNodes: state.nodes.length,
            totalConnections: state.connections.length,
            nodeTypes: {},
            hasStart: state.nodes.some(n => n.isStart),
            startNodeId: state.nodes.find(n => n.isStart)?.id
        };
        
        // Count by type
        state.nodes.forEach(node => {
            stats.nodeTypes[node.type] = (stats.nodeTypes[node.type] || 0) + 1;
        });
        
        // Connection type breakdown
        stats.connectionTypes = {
            pass: state.connections.filter(c => c.type === 'pass').length,
            fail: state.connections.filter(c => c.type === 'fail').length,
            complete: state.connections.filter(c => c.type === 'complete').length
        };
        
        return stats;
    }
    
    async sendMessage(overrideMessage) {
        const input = document.getElementById('builderInput');
        const message = overrideMessage || input.value.trim();

        if (!message) return;

        // Add user message to chat
        this.addMessage(message, 'user');

        // Clear input
        input.value = '';
        input.style.height = 'auto';
        
        // Disable send button and show typing
        document.getElementById('sendButton').disabled = true;
        this.showTypingIndicator();
        
        try {
            // BUILD WORKFLOW CONTEXT
            const workflowContext = this.buildWorkflowContext();

            // Call backend
            const response = await fetch('/api/workflow/builder/guide', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    message: message,
                    session_id: this.sessionId,
                    workflow_state: workflowContext
                })
            });
            
            const data = await response.json();

            console.log('Response received:', data);
            
            // Remove typing indicator
            this.hideTypingIndicator();
            
            // Add assistant response
            this.addMessage(data.response, 'assistant');
            
            // Update UI based on phase
            if (data.phase) {
                this.updatePhase(data.phase);
            }
            
            // Update requirements display
            if (data.requirements) {
                this.updateRequirements(data.requirements);
            }
            
            // Execute workflow commands if returned
            if (data.workflow_commands) {
                this.executeGeneratedCommands(data.workflow_commands);
            }
            
        } catch (error) {
            console.error('Error:', error);
            this.hideTypingIndicator();
            this.addMessage('Sorry, I encountered an error. Please try again.', 'error');
        } finally {
            // Re-enable send button
            document.getElementById('sendButton').disabled = false;
            document.getElementById('builderInput').focus();
        }
    }
    
    addMessage(text, type) {
        const messagesContainer = document.getElementById('builderMessages');
        
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${type} fade-in`;
        
        const avatarIcon = type === 'user' ? 
            `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
                <circle cx="12" cy="7" r="4"></circle>
            </svg>` :
            `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"></circle>
                <path d="M8 14s1.5 2 4 2 4-2 4-2"></path>
                <line x1="9" y1="9" x2="9.01" y2="9"></line>
                <line x1="15" y1="9" x2="15.01" y2="9"></line>
            </svg>`;
        
        const header = type === 'user' ? 'You' : 'AI Assistant';
        
        messageDiv.innerHTML = `
            <div class="message-avatar">${avatarIcon}</div>
            <div class="message-content">
                <div class="message-header">${header}</div>
                <div class="message-text">${this.formatMessage(text)}</div>
            </div>
        `;
        
        messagesContainer.appendChild(messageDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
    
    formatMessage_legacy(text) {
        // Convert line breaks and format lists
        return text
            .replace(/\n/g, '<br>')
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.*?)\*/g, '<em>$1</em>')
            .replace(/^(\d+\.\s)/gm, '<br>$1');
    }

    /**
     * Enhanced message formatting with collapsible code block support
     * This replaces the existing formatMessage() method
     */
    formatMessage(text) {
        // Extract workflow plans and replace with placeholders
        const workflowPlans = [];
        let processedText = text.replace(/<workflow_plan>([\s\S]*?)<\/workflow_plan>/g, (match, content) => {
            const planId = `workflowplan_${Date.now()}_${workflowPlans.length}`;
            workflowPlans.push({ id: planId, content: content.trim() });
            return `__WORKFLOWPLAN_${planId}__`;
        });

        // Extract code blocks and replace with placeholders
        const codeBlocks = [];
        
        // SINGLE PASS: Handle ALL code blocks at once to avoid conflicts
        processedText = text.replace(/```(\w+)?\s*([\s\S]*?)```/g, (match, language, content) => {
            const blockId = `codeblock_${Date.now()}_${codeBlocks.length}`;
            
            // Check if it's JSON (either explicitly marked or contains JSON structure)
            const isJson = language === 'json' || (language === undefined && content.trim().startsWith('{'));
            
            codeBlocks.push({ 
                id: blockId, 
                content: content.trim(), 
                language: language || 'text',
                isJson: isJson 
            });
            
            return `__CODEBLOCK_${blockId}__`;
        });
        
        // Apply standard markdown formatting
        processedText = processedText
            .replace(/\n\n/g, '<br><br>')  // Paragraph breaks
            .replace(/\n/g, '<br>')         // Line breaks
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')  // Bold
            .replace(/\*(.*?)\*/g, '<em>$1</em>')              // Italic
            .replace(/^(\d+\.\s)/gm, '<br>$1');                // Numbered lists
        
        // Replace placeholders with collapsible code blocks
        codeBlocks.forEach(block => {
            const collapsibleHtml = this.createCollapsibleCodeBlock(block);
            processedText = processedText.replace(`__CODEBLOCK_${block.id}__`, collapsibleHtml);
        });

        // Replace placeholders with styled workflow plans
        workflowPlans.forEach(plan => {
            const planHtml = this.createWorkflowPlanBlock(plan);
            processedText = processedText.replace(`__WORKFLOWPLAN_${plan.id}__`, planHtml);
        });
        
        return processedText;
    }

    /**
     * Creates a collapsible container for code blocks
     */
    createCollapsibleCodeBlock(block) {
        const blockId = `collapse_${block.id}`;
        
        // For JSON blocks, try to parse and extract metadata
        let displayContent = block.content;
        let commandCount = 0;
        let summary = 'Code Block';
        let iconClass = 'bi-code-square';
        
        if (block.isJson) {
            try {
                const parsed = JSON.parse(block.content);
                
                // Check if it's workflow commands
                if (parsed.action === 'build_workflow' && Array.isArray(parsed.commands)) {
                    //commandCount = parsed.commands.length;
                    // COUNT ONLY add_node COMMANDS (actual workflow steps)
                    commandCount = parsed.commands.filter(cmd => cmd.type === 'add_node').length;
                    summary = `Workflow Build Commands (${commandCount} step${commandCount !== 1 ? 's' : ''})`;
                    iconClass = 'bi-diagram-3';
                    
                    // Pretty print the JSON
                    displayContent = JSON.stringify(parsed, null, 2);
                } else if (Array.isArray(parsed.commands)) {
                    // COUNT ONLY add_node COMMANDS (actual workflow steps)
                    commandCount = parsed.commands.filter(cmd => cmd.type === 'add_node').length;
                    summary = `Commands (${commandCount})`;
                    displayContent = JSON.stringify(parsed, null, 2);
                } else {
                    summary = 'JSON Data';
                    displayContent = JSON.stringify(parsed, null, 2);
                }
            } catch (e) {
                // If parsing fails, just display as-is
                summary = 'JSON Code Block';
                console.warn('Failed to parse JSON in code block:', e);
            }
        } else if (block.language) {
            summary = `${block.language.toUpperCase()} Code`;
        }
        
        return `
            <div class="workflow-commands-container" data-block-id="${blockId}">
                <button class="workflow-commands-toggle" 
                        type="button" 
                        onclick="this.classList.toggle('expanded'); 
                                document.getElementById('${blockId}').classList.toggle('show')"
                        aria-expanded="false"
                        aria-controls="${blockId}">
                    <span class="toggle-header">
                        <i class="bi ${iconClass}"></i>
                        <span class="toggle-title">${summary}</span>
                    </span>
                    <i class="bi bi-chevron-down toggle-icon"></i>
                </button>
                <div class="workflow-commands-content collapse" id="${blockId}">
                    <div class="commands-toolbar">
                        <button class="btn btn-sm btn-outline-light copy-btn" 
                                onclick="window.workflowBuilder.copyCodeBlock('${blockId}_code')"
                                title="Copy to clipboard">
                            <i class="bi bi-clipboard"></i> Copy
                        </button>
                    </div>
                    <pre><code id="${blockId}_code" class="language-json">${this.escapeHtml(displayContent)}</code></pre>
                </div>
            </div>
        `;
    }

    /**
     * Creates a styled container for workflow plans
     */
    createWorkflowPlanBlock(plan) {
        // Parse the plan content into steps
        const lines = plan.content.split('\n').filter(line => line.trim());
        let stepsHtml = '';
        
        lines.forEach(line => {
            const trimmed = line.trim();
            
            // Check if it's a branch item (starts with - or •)
            if (trimmed.match(/^[-•]\s*(If|When|On)/i)) {
                stepsHtml += `<div class="branch-item">${this.escapeHtml(trimmed.replace(/^[-•]\s*/, ''))}</div>`;
            }
            // Check if it's a numbered step
            else if (trimmed.match(/^\d+\./)) {
                const match = trimmed.match(/^(\d+)\.\s*(.*)/);
                if (match) {
                    stepsHtml += `
                        <div class="plan-step">
                            <span class="step-number">${match[1]}.</span>
                            <span class="step-text">${this.escapeHtml(match[2])}</span>
                        </div>`;
                }
            }
            // Other lines (like sub-items)
            else if (trimmed.startsWith('-') || trimmed.startsWith('•')) {
                stepsHtml += `<div class="branch-item">${this.escapeHtml(trimmed.replace(/^[-•]\s*/, ''))}</div>`;
            }
            else if (trimmed) {
                stepsHtml += `<div class="plan-step"><span class="step-text">${this.escapeHtml(trimmed)}</span></div>`;
            }
        });

        return `
            <div class="workflow-plan-container">
                <div class="workflow-plan-header">
                    <i class="bi bi-diagram-2"></i>
                    <span>Workflow Plan</span>
                </div>
                <div class="workflow-plan-content">
                    ${stepsHtml}
                </div>
            </div>
        `;
    }

    /**
     * Escapes HTML special characters
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * Copy code block to clipboard
     */
    copyToClipboard(elementId) {
        const codeElement = document.getElementById(elementId);
        if (!codeElement) return;
        
        const text = codeElement.textContent;
        
        // Use modern clipboard API if available
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(() => {
                this.showNotification('Copied to clipboard!', 'success');
            }).catch(err => {
                console.error('Failed to copy:', err);
                this.fallbackCopy(text);
            });
        } else {
            this.fallbackCopy(text);
        }
    }

    copyCodeBlock(elementId) {
        const codeElement = document.getElementById(elementId);
        if (!codeElement) {
            console.error('Code element not found:', elementId);
            return;
        }
        
        // Get the text content (unescaped HTML entities automatically)
        const text = codeElement.textContent;
        
        // Use modern clipboard API if available
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(() => {
                this.showCopySuccess(elementId);
            }).catch(err => {
                console.error('Failed to copy:', err);
                this.fallbackCopy(text);
            });
        } else {
            this.fallbackCopy(text);
        }
    }

    showCopySuccess(elementId) {
        // Show visual feedback
        const btn = document.querySelector(`button[onclick*="${elementId}"]`);
        if (btn) {
            const originalHTML = btn.innerHTML;
            btn.innerHTML = '<i class="bi bi-check"></i> Copied!';
            btn.classList.add('btn-success');
            btn.classList.remove('btn-outline-light');
            
            setTimeout(() => {
                btn.innerHTML = originalHTML;
                btn.classList.remove('btn-success');
                btn.classList.add('btn-outline-light');
            }, 2000);
        }
        
        // Also show toast notification
        this.showNotification('Copied to clipboard!', 'success');
    }

    fallbackCopy(text) {
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.style.position = 'fixed';
        textarea.style.left = '-9999px';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        textarea.setSelectionRange(0, 99999); // For mobile devices
        
        try {
            const successful = document.execCommand('copy');
            if (successful) {
                this.showNotification('Copied to clipboard!', 'success');
            } else {
                this.showNotification('Failed to copy. Please copy manually.', 'error');
            }
        } catch (err) {
            console.error('Fallback copy failed:', err);
            this.showNotification('Failed to copy. Please copy manually.', 'error');
        }
        
        document.body.removeChild(textarea);
    }

    
    showTypingIndicator() {
        const messagesContainer = document.getElementById('builderMessages');
        const typingDiv = document.createElement('div');
        typingDiv.className = 'message assistant typing-message';
        typingDiv.innerHTML = `
            <div class="message-avatar">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10"></circle>
                    <path d="M8 14s1.5 2 4 2 4-2 4-2"></path>
                    <line x1="9" y1="9" x2="9.01" y2="9"></line>
                    <line x1="15" y1="9" x2="15.01" y2="9"></line>
                </svg>
            </div>
            <div class="message-content">
                <div class="typing-indicator">
                    <div class="typing-dot"></div>
                    <div class="typing-dot"></div>
                    <div class="typing-dot"></div>
                </div>
            </div>
        `;
        messagesContainer.appendChild(typingDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
    
    hideTypingIndicator() {
        const typingMessage = document.querySelector('.typing-message');
        if (typingMessage) {
            typingMessage.remove();
        }
    }
    
    updatePhase(phase) {
        this.currentPhase = phase;
        
        const phaseMap = {
            'discovery': { width: '20%', label: 'Discovery' },
            'requirements': { width: '40%', label: 'Gathering Requirements' },
            'planning': { width: '60%', label: 'Planning Workflow' },
            'building': { width: '80%', label: 'Building Workflow' },
            'refinement': { width: '100%', label: 'Refining & Optimizing' }
        };
        
        const phaseInfo = phaseMap[phase];
        if (phaseInfo) {
            // Update progress bar
            const progressBar = document.getElementById('progressBar');
            progressBar.style.width = phaseInfo.width;
            progressBar.querySelector('.progress-label').textContent = phaseInfo.label;
            
            // Update phase badge
            document.getElementById('phaseBadge').textContent = phaseInfo.label;
            
            // Update phase steps
            document.querySelectorAll('.phase-step').forEach(step => {
                const stepPhase = step.dataset.phase;
                const phaseOrder = ['discovery', 'requirements', 'planning', 'building', 'refinement'];
                const currentIndex = phaseOrder.indexOf(phase);
                const stepIndex = phaseOrder.indexOf(stepPhase);
                
                if (stepIndex <= currentIndex) {
                    step.classList.add('active');
                } else {
                    step.classList.remove('active');
                }
            });
            
            // Show build button when appropriate
            if (phase === 'planning' || phase === 'building' || phase === 'refinement') {
                document.getElementById('buildWorkflowBtn').style.display = 'block';
            }
        }
    }
    
    updateRequirements(requirements) {
        this.requirements = requirements;
        const listContainer = document.getElementById('requirementsList');
        
        // Clear existing items
        listContainer.innerHTML = '';
        
        // Add requirement items
        const items = [];
        
        if (requirements.process_name) {
            items.push(`<strong>Process:</strong> ${requirements.process_name}`);
        }
        
        if (requirements.trigger_type) {
            items.push(`<strong>Trigger:</strong> ${requirements.trigger_type}`);
        }
        
        if (requirements.data_sources && requirements.data_sources.length > 0) {
            items.push(`<strong>Data Sources:</strong> ${requirements.data_sources.length} identified`);
        }
        
        if (requirements.stakeholders && requirements.stakeholders.length > 0) {
            items.push(`<strong>Stakeholders:</strong> ${requirements.stakeholders.join(', ')}`);
        }
        
        if (requirements.outputs && requirements.outputs.length > 0) {
            items.push(`<strong>Outputs:</strong> ${requirements.outputs.length} defined`);
        }
        
        if (items.length === 0) {
            listContainer.innerHTML = `
                <div class="requirement-item empty">
                    <span>Requirements will appear here as we discover them...</span>
                </div>
            `;
        } else {
            items.forEach(item => {
                const div = document.createElement('div');
                div.className = 'requirement-item';
                div.innerHTML = item;
                listContainer.appendChild(div);
            });
        }
    }
    
    async buildWorkflow() {
        // Request workflow generation
        this.sendMessage("Please generate the workflow now based on what we've discussed.");
    }
    
    executeGeneratedCommands(commands) {
        console.log('Executing workflow commands:', commands);
        
        // Check if the global executeWorkflowCommands function exists
        if (window.executeWorkflowCommands) {
            try {
                // The executeWorkflowCommands expects a JSON string, not an object
                // If commands is already an object with action and commands properties, stringify it
                let commandsToExecute;
                if (typeof commands === 'object' && commands !== null) {
                    // If it's already the full structure with action and commands
                    if (commands.action && commands.commands) {
                        commandsToExecute = JSON.stringify(commands);
                    } else {
                        // If it's just the commands array, wrap it
                        commandsToExecute = JSON.stringify({
                            action: "build_workflow",
                            commands: commands
                        });
                    }
                } else if (typeof commands === 'string') {
                    // If it's already a string, use it as is
                    commandsToExecute = commands;
                } else {
                    throw new Error('Invalid commands format');
                }
                
                console.log('Executing commands string:', commandsToExecute);

                // Close modal FIRST
                setTimeout(() => {
                    this.modal.hide();
                }, 200);

                    // Show impressive building animation sequence
                    this.showBuildingAnimation(async () => {
                        // Execute commands in the main workflow designer
                        await window.executeWorkflowCommands(commandsToExecute);

                        // Mark workflow as successfully built
                        this.workflowBuiltSuccessfully = true;
                        this.showExportButtonIfEnabled();
                        
                        // Show success notification
                        setTimeout(() => {
                            this.showNotification('Workflow created successfully! Check the canvas for your new workflow.');
                        }, 500);
                    });
            } catch (error) {
                console.error('Error executing workflow commands:', error);
                this.showNotification('Error executing workflow commands. Check console for details.', 'error');
            }
        } else {
            console.error('executeWorkflowCommands function not found in workflow tool');
            console.error('Make sure workflow_command_executor.js is loaded');
            this.showNotification('Error: Workflow execution function not found. Please check integration.', 'error');
            
            // Log the commands for manual debugging
            console.log('Generated commands that could not be executed:', JSON.stringify(commands, null, 2));
            
            // Show the commands to the user as a fallback
            alert('Workflow commands were generated but could not be executed automatically. Check the console for the commands.');
        }
    }

        
    showBuildingAnimation(onComplete) {
        // Navy Blue colors
        const overlayColor1 = '#01579b';
        const overlayColor2 = '#0277bd';
        const overlayColor3 = '#01579b';
        
        // Settings
        const initialOpacity = 0.85;
        const finalOpacity = 0.40;
        const totalAnimationTime = 6000; // Total time for the entire animation (6 seconds)
        const overlayDuration = 2000; // Initial message rotation time (2 seconds)
        
        // Helper function to convert hex to rgba
        function hexToRgba(hex, alpha) {
            const r = parseInt(hex.slice(1, 3), 16);
            const g = parseInt(hex.slice(3, 5), 16);
            const b = parseInt(hex.slice(5, 7), 16);
            return `rgba(${r}, ${g}, ${b}, ${alpha})`;
        }
        
        // Create full-screen overlay for the animation
        const overlay = document.createElement('div');
        overlay.className = 'workflow-building-overlay-v2';
        overlay.id = 'workflowBuildingOverlay';
        
        // Apply initial colors with initial opacity
        const color1Init = hexToRgba(overlayColor1, initialOpacity);
        const color2Init = hexToRgba(overlayColor2, initialOpacity);
        const color3Init = hexToRgba(overlayColor3, initialOpacity);
        
        overlay.style.background = `linear-gradient(135deg, ${color1Init} 0%, ${color2Init} 50%, ${color3Init} 100%)`;
        overlay.style.backgroundSize = '200% 200%';
        
        // Set up the gradual fade transition immediately but it won't start until we change the background
        const fadeSpeed = (totalAnimationTime - overlayDuration) / 1000; // Convert to seconds
        //overlay.style.transition = `background ${fadeSpeed}s ease`;
        overlay.style.transition = `opacity 0.5s ease, background ${fadeSpeed}s ease`;
        
        overlay.innerHTML = `
            <div class="ai-working-container">
                <div class="ai-status" style="margin-top: 0;">
                    <h3 class="ai-title">AI Processing</h3>
                    <p class="ai-message">Analyzing your requirements and generating workflow...</p>
                </div>
            </div>
        `;
        
        document.body.appendChild(overlay);
        
        // Simple message rotation
        const messages = [
            "Analyzing your requirements and generating workflow...",
            "Preparing workflow structure...",
            "Ready to build..."
        ];
        
        let messageIndex = 0;
        const messageElement = overlay.querySelector('.ai-message');
        
        // Animate in the overlay
        setTimeout(() => overlay.classList.add('active'), 50);
        
        // Rotate messages
        const messageInterval = setInterval(() => {
            messageIndex = (messageIndex + 1) % messages.length;
            messageElement.style.opacity = '0';
            setTimeout(() => {
                messageElement.textContent = messages[messageIndex];
                messageElement.style.opacity = '1';
            }, 300);
        }, 1500);
        
        // Start the gradual fade immediately after overlay appears
        setTimeout(() => {
            // Trigger the gradual fade to final opacity
            const color1Final = hexToRgba(overlayColor1, finalOpacity);
            const color2Final = hexToRgba(overlayColor2, finalOpacity);
            const color3Final = hexToRgba(overlayColor3, finalOpacity);
            
            overlay.style.background = `linear-gradient(135deg, ${color1Final} 0%, ${color2Final} 50%, ${color3Final} 100%)`;
        }, 100);
        
        // Complete initial animation, then start building
        setTimeout(() => {
            clearInterval(messageInterval);
            
            // Update message to show building is starting
            messageElement.style.opacity = '0';
            setTimeout(() => {
                messageElement.textContent = "Building workflow...";
                messageElement.style.opacity = '1';
            }, 300);
            
            // Execute the actual workflow commands with animation
            setTimeout(async () => {
                await onComplete();  // Waits until done
                
                // Keep overlay visible for a moment to show completion
                setTimeout(() => {
                    // Show completion
                    overlay.querySelector('.ai-title').textContent = "Complete";
                    messageElement.textContent = "Workflow ready!";
                    //overlay.querySelector('.brain-core').classList.add('complete');
                    
                    // Fade out overlay after brief pause
                    setTimeout(() => {
                        overlay.classList.add('fade-out');
                        setTimeout(() => overlay.remove(), 500);
                    }, 800);
                }, 500);
            }, 500);
            
        }, overlayDuration);
    }
    
    showNotification(message, type = 'success') {
        // Create toast notification
        const toast = document.createElement('div');
        toast.className = `toast align-items-center text-white bg-${type === 'success' ? 'success' : 'danger'} border-0`;
        toast.setAttribute('role', 'alert');
        toast.innerHTML = `
            <div class="d-flex">
                <div class="toast-body">${message}</div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        `;
        
        // Add to container or create one
        let container = document.querySelector('.toast-container');
        if (!container) {
            container = document.createElement('div');
            container.className = 'toast-container position-fixed bottom-0 end-0 p-3';
            document.body.appendChild(container);
        }
        
        container.appendChild(toast);
        const bsToast = new bootstrap.Toast(toast);
        bsToast.show();
    }

    resetSession() {
        /**
         * Reset the AI Builder session
         * Called when user creates a new workflow to clear cached state
         */
        
        // Generate a new session ID
        const oldSessionId = this.sessionId;
        this.sessionId = Date.now().toString();
        window.workflowBuilderSessionId = this.sessionId;  // Expose for command executor
        
        console.log(`AI Builder session reset: ${oldSessionId} -> ${this.sessionId}`);
        
        // Clear local state
        this.currentPhase = 'discovery';
        this.requirements = {};
        this.workflowBuiltSuccessfully = false;

        // Hide export button
        const exportBtn = document.getElementById('exportTrainingBtn');
        if (exportBtn) {
            exportBtn.style.display = 'none';
            exportBtn.disabled = false;
            exportBtn.innerHTML = '<i class="bi bi-database-add"></i> Export Training';
            exportBtn.classList.remove('btn-success');
            exportBtn.classList.add('btn-outline-light');
        }
        
        // Clear chat history if modal is open
        if (this.isOpen) {
            const messagesContainer = document.getElementById('builderMessages');
            if (messagesContainer) {
                messagesContainer.innerHTML = '';
                
                // Add fresh greeting for build mode
                this.addMessage(
                    `Hi! I'm here to help you build a workflow that automates your business process. ` +
                    `Let's start by understanding what you'd like to automate.\n\n` +
                    `What process or task are you looking to streamline?`,
                    'assistant'
                );
            }
            
            // Reset phase display
            this.updatePhase('discovery');
            
            // Clear requirements sidebar
            const requirementsList = document.getElementById('requirementsList');
            if (requirementsList) {
                requirementsList.innerHTML = `
                    <div class="requirement-item empty">
                        <span>Requirements will appear here as we discover them...</span>
                    </div>
                `;
            }
        }
        
        // Clear backend session
        this.clearBackendSession(oldSessionId);
    }

    async clearBackendSession(sessionId) {
        /**
         * Clear the backend session for this AI builder instance
         */
        try {
            await fetch('/api/workflow/builder/clear', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: sessionId })
            });
            console.log('Backend session cleared:', sessionId);
        } catch (error) {
            console.warn('Failed to clear backend session:', error);
            // Non-critical error, continue anyway
        }
    }

    showExportButtonIfEnabled() {
        /**
         * Show the export training button if capture is enabled and workflow was built
         */
        const exportBtn = document.getElementById('exportTrainingBtn');
        if (exportBtn && this.trainingCaptureEnabled && this.workflowBuiltSuccessfully) {
            exportBtn.style.display = 'inline-flex';
            console.log('Export training button shown');
        }
    }

    async exportTrainingData() {
        /**
         * Export the current conversation to training dataset
         */
        const exportBtn = document.getElementById('exportTrainingBtn');
        if (exportBtn) {
            exportBtn.disabled = true;
            exportBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> Exporting...';
        }

        try {
            const response = await fetch('/api/workflow/builder/finalize-capture', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    success: true
                })
            });

            const data = await response.json();

            if (data.captured) {
                this.showNotification('Conversation exported to training dataset!', 'success');
                if (exportBtn) {
                    exportBtn.innerHTML = '<i class="bi bi-check-circle"></i> Exported';
                    exportBtn.classList.remove('btn-outline-light');
                    exportBtn.classList.add('btn-success');
                }
            } else {
                this.showNotification('No training data captured (may already be exported or no commands found)', 'warning');
                if (exportBtn) {
                    exportBtn.innerHTML = '<i class="bi bi-database-add"></i> Export Training';
                    exportBtn.disabled = false;
                }
            }
        } catch (error) {
            console.error('Error exporting training data:', error);
            this.showNotification('Error exporting training data', 'error');
            if (exportBtn) {
                exportBtn.innerHTML = '<i class="bi bi-database-add"></i> Export Training';
                exportBtn.disabled = false;
            }
        }
    }
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.workflowBuilder = new WorkflowBuilderGuide();
    });
} else {
    window.workflowBuilder = new WorkflowBuilderGuide();
}
