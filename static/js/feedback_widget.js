/**
 * Feedback Widget - In-App Feedback System
 * =========================================
 * Provides a modal for users to submit bug reports, feature requests, 
 * questions, and general feedback. Triggered from user menu.
 * 
 * Features:
 * - Modal form with feedback type selection
 * - Auto-captures page context (URL, title)
 * - Optional diagnostic information (with user consent)
 * - Recent error tracking for bug reports
 * - Success/error notifications
 * 
 * Usage:
 * Add a menu item that calls FeedbackWidget.open() or use data-action="open-feedback"
 */

(function() {
    'use strict';

    // Configuration
    const CONFIG = {
        maxDescriptionLength: 10000,
        maxSubjectLength: 255,
        maxRecentErrors: 10,
        errorRetentionMs: 5 * 60 * 1000, // 5 minutes
        submitEndpoint: '/api/feedback/submit',
        animationDuration: 300
    };

    // State
    const state = {
        isOpen: false,
        isSubmitting: false,
        recentErrors: [],
        initialized: false
    };

    // ===========================================
    // Error Tracking
    // ===========================================

    /**
     * Capture and store recent JavaScript errors
     */
    function setupErrorTracking() {
        const originalOnError = window.onerror;
        
        window.onerror = function(message, source, lineno, colno, error) {
            const errorEntry = {
                message: message,
                source: source,
                line: lineno,
                column: colno,
                stack: error?.stack,
                timestamp: new Date().toISOString()
            };
            
            state.recentErrors.push(errorEntry);
            
            // Keep only recent errors
            const cutoff = Date.now() - CONFIG.errorRetentionMs;
            state.recentErrors = state.recentErrors.filter(e => 
                new Date(e.timestamp).getTime() > cutoff
            ).slice(-CONFIG.maxRecentErrors);
            
            // Call original handler if exists
            if (originalOnError) {
                return originalOnError.apply(this, arguments);
            }
            return false;
        };

        // Also capture unhandled promise rejections
        window.addEventListener('unhandledrejection', function(event) {
            const errorEntry = {
                message: 'Unhandled Promise Rejection: ' + event.reason,
                type: 'promise_rejection',
                timestamp: new Date().toISOString()
            };
            
            state.recentErrors.push(errorEntry);
            state.recentErrors = state.recentErrors.slice(-CONFIG.maxRecentErrors);
        });
    }

    // ===========================================
    // UI Creation
    // ===========================================

    /**
     * Create and inject the feedback modal HTML
     */
    function createFeedbackModal() {
        // Check if already exists
        if (document.getElementById('feedback-widget-container')) {
            return;
        }

        const container = document.createElement('div');
        container.id = 'feedback-widget-container';
        container.innerHTML = `
            <!-- Feedback Modal -->
            <div id="feedback-modal" class="feedback-modal" role="dialog" aria-modal="true" aria-labelledby="feedback-modal-title">
                <div class="feedback-modal-backdrop"></div>
                <div class="feedback-modal-content">
                    <div class="feedback-modal-header">
                        <h5 id="feedback-modal-title">
                            <i class="fas fa-paper-plane mr-2"></i>Send Feedback
                        </h5>
                        <button type="button" class="feedback-modal-close" aria-label="Close">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    
                    <div class="feedback-modal-body">
                        <form id="feedback-form">
                            <!-- Feedback Type Selection -->
                            <div class="feedback-type-selector">
                                <label class="feedback-type-option" data-type="bug">
                                    <input type="radio" name="feedback_type" value="bug">
                                    <div class="feedback-type-card">
                                        <i class="fas fa-bug"></i>
                                        <span>Bug Report</span>
                                    </div>
                                </label>
                                <label class="feedback-type-option" data-type="feature">
                                    <input type="radio" name="feedback_type" value="feature">
                                    <div class="feedback-type-card">
                                        <i class="fas fa-lightbulb"></i>
                                        <span>Feature Request</span>
                                    </div>
                                </label>
                                <label class="feedback-type-option" data-type="question">
                                    <input type="radio" name="feedback_type" value="question">
                                    <div class="feedback-type-card">
                                        <i class="fas fa-question-circle"></i>
                                        <span>Question</span>
                                    </div>
                                </label>
                                <label class="feedback-type-option" data-type="general">
                                    <input type="radio" name="feedback_type" value="general" checked>
                                    <div class="feedback-type-card">
                                        <i class="fas fa-comment"></i>
                                        <span>General</span>
                                    </div>
                                </label>
                            </div>

                            <!-- Subject (Optional) -->
                            <div class="form-group">
                                <label for="feedback-subject">Subject <span class="text-muted">(optional)</span></label>
                                <input type="text" id="feedback-subject" class="form-control" 
                                       placeholder="Brief summary of your feedback"
                                       maxlength="${CONFIG.maxSubjectLength}">
                            </div>

                            <!-- Description -->
                            <div class="form-group">
                                <label for="feedback-description">Description <span class="text-danger">*</span></label>
                                <textarea id="feedback-description" class="form-control" rows="5"
                                          placeholder="Please describe your feedback in detail..."
                                          maxlength="${CONFIG.maxDescriptionLength}" required></textarea>
                                <small class="form-text text-muted">
                                    <span id="feedback-char-count">0</span> / ${CONFIG.maxDescriptionLength.toLocaleString()} characters
                                </small>
                            </div>

                            <!-- Priority (shown for bugs) -->
                            <div class="form-group" id="feedback-priority-group" style="display: none;">
                                <label for="feedback-priority">Priority</label>
                                <select id="feedback-priority" class="form-control">
                                    <option value="low">Low - Minor issue</option>
                                    <option value="medium" selected>Medium - Affects work</option>
                                    <option value="high">High - Blocking work</option>
                                    <option value="critical">Critical - Urgent</option>
                                </select>
                            </div>

                            <!-- Diagnostics Opt-in -->
                            <div class="form-group">
                                <div class="custom-control custom-checkbox">
                                    <input type="checkbox" class="custom-control-input" id="feedback-include-diagnostics">
                                    <label class="custom-control-label" for="feedback-include-diagnostics">
                                        Include diagnostic information
                                        <i class="fas fa-info-circle text-muted ml-1" 
                                           data-toggle="tooltip"
                                           title="Includes current page, browser info, and recent errors to help us investigate"></i>
                                    </label>
                                </div>
                                <small class="form-text text-muted">
                                    Helps us investigate issues faster. Includes page URL, browser info, and recent errors.
                                </small>
                            </div>

                            <!-- Context Info Preview (shown when diagnostics enabled) -->
                            <div id="feedback-diagnostics-preview" class="feedback-diagnostics-preview" style="display: none;">
                                <div class="feedback-diagnostics-header">
                                    <i class="fas fa-info-circle"></i> Information that will be included:
                                </div>
                                <ul class="feedback-diagnostics-list">
                                    <li><strong>Page:</strong> <span id="diag-page-url"></span></li>
                                    <li><strong>Browser:</strong> <span id="diag-browser"></span></li>
                                    <li><strong>Screen:</strong> <span id="diag-screen"></span></li>
                                    <li id="diag-errors-item" style="display: none;">
                                        <strong>Recent Errors:</strong> <span id="diag-errors-count"></span>
                                    </li>
                                </ul>
                            </div>
                        </form>
                    </div>
                    
                    <div class="feedback-modal-footer">
                        <button type="button" class="btn btn-secondary" id="feedback-cancel-btn">
                            Cancel
                        </button>
                        <button type="submit" class="btn btn-primary" id="feedback-submit-btn" form="feedback-form">
                            <span class="feedback-submit-text">
                                <i class="fas fa-paper-plane mr-1"></i> Send Feedback
                            </span>
                            <span class="feedback-submit-loading" style="display: none;">
                                <i class="fas fa-spinner fa-spin mr-1"></i> Sending...
                            </span>
                        </button>
                    </div>
                </div>
            </div>

            <!-- Success/Error Toast -->
            <div id="feedback-toast" class="feedback-toast" role="alert" aria-live="polite">
                <div class="feedback-toast-icon"></div>
                <div class="feedback-toast-message"></div>
                <button type="button" class="feedback-toast-close" aria-label="Close">
                    <i class="fas fa-times"></i>
                </button>
            </div>
        `;

        document.body.appendChild(container);
    }

    // ===========================================
    // Event Handlers
    // ===========================================

    /**
     * Setup all event listeners
     */
    function setupEventListeners() {
        // Close button
        document.querySelector('.feedback-modal-close').addEventListener('click', closeModal);

        // Cancel button
        document.getElementById('feedback-cancel-btn').addEventListener('click', closeModal);

        // Backdrop click
        document.querySelector('.feedback-modal-backdrop').addEventListener('click', closeModal);

        // Form submission
        document.getElementById('feedback-form').addEventListener('submit', handleSubmit);

        // Feedback type change
        document.querySelectorAll('input[name="feedback_type"]').forEach(radio => {
            radio.addEventListener('change', handleTypeChange);
        });

        // Description character count
        document.getElementById('feedback-description').addEventListener('input', updateCharCount);

        // Diagnostics toggle
        document.getElementById('feedback-include-diagnostics').addEventListener('change', toggleDiagnosticsPreview);

        // Toast close
        document.querySelector('.feedback-toast-close').addEventListener('click', hideToast);

        // Escape key to close
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && state.isOpen) {
                closeModal();
            }
        });

        // Listen for menu item clicks with data-action="open-feedback"
        document.addEventListener('click', (e) => {
            const feedbackTrigger = e.target.closest('[data-action="open-feedback"]');
            if (feedbackTrigger) {
                e.preventDefault();
                openModal();
            }
        });
    }

    /**
     * Open the feedback modal
     */
    function openModal() {
        // Ensure widget is initialized
        if (!state.initialized) {
            init();
        }

        const modal = document.getElementById('feedback-modal');
        state.isOpen = true;
        modal.classList.add('show');
        document.body.style.overflow = 'hidden';
        
        // Focus first input
        setTimeout(() => {
            document.getElementById('feedback-subject').focus();
        }, CONFIG.animationDuration);

        // Update diagnostics preview
        updateDiagnosticsPreview();
    }

    /**
     * Close the feedback modal
     */
    function closeModal() {
        if (state.isSubmitting) return;
        
        const modal = document.getElementById('feedback-modal');
        state.isOpen = false;
        modal.classList.remove('show');
        document.body.style.overflow = '';
        
        // Reset form after animation
        setTimeout(() => {
            resetForm();
        }, CONFIG.animationDuration);
    }

    /**
     * Handle feedback type change
     */
    function handleTypeChange(e) {
        const type = e.target.value;
        const priorityGroup = document.getElementById('feedback-priority-group');
        
        // Show priority selector for bug reports
        if (type === 'bug') {
            priorityGroup.style.display = 'block';
            // Auto-check diagnostics for bugs
            document.getElementById('feedback-include-diagnostics').checked = true;
            toggleDiagnosticsPreview();
        } else {
            priorityGroup.style.display = 'none';
        }
        
        // Update placeholder based on type
        const descriptionField = document.getElementById('feedback-description');
        const placeholders = {
            bug: 'Please describe the issue you encountered. Include:\n• What you were trying to do\n• What happened instead\n• Steps to reproduce the issue',
            feature: 'Please describe the feature you\'d like to see:\n• What problem would it solve?\n• How would you use it?',
            question: 'What would you like to know? We\'ll get back to you as soon as possible.',
            general: 'Share your thoughts, suggestions, or any other feedback...'
        };
        descriptionField.placeholder = placeholders[type] || placeholders.general;
    }

    /**
     * Update character count display
     */
    function updateCharCount() {
        const description = document.getElementById('feedback-description');
        const count = document.getElementById('feedback-char-count');
        count.textContent = description.value.length.toLocaleString();
    }

    /**
     * Toggle diagnostics preview visibility
     */
    function toggleDiagnosticsPreview() {
        const checkbox = document.getElementById('feedback-include-diagnostics');
        const preview = document.getElementById('feedback-diagnostics-preview');
        
        if (checkbox.checked) {
            preview.style.display = 'block';
            updateDiagnosticsPreview();
        } else {
            preview.style.display = 'none';
        }
    }

    /**
     * Update the diagnostics preview with current values
     */
    function updateDiagnosticsPreview() {
        document.getElementById('diag-page-url').textContent = window.location.pathname;
        document.getElementById('diag-browser').textContent = getBrowserName();
        document.getElementById('diag-screen').textContent = `${window.screen.width}x${window.screen.height}`;
        
        const errorsItem = document.getElementById('diag-errors-item');
        const errorsCount = document.getElementById('diag-errors-count');
        
        if (state.recentErrors.length > 0) {
            errorsItem.style.display = 'list-item';
            errorsCount.textContent = `${state.recentErrors.length} error(s) captured`;
        } else {
            errorsItem.style.display = 'none';
        }
    }

    /**
     * Get simplified browser name
     */
    function getBrowserName() {
        const ua = navigator.userAgent;
        if (ua.includes('Chrome')) return 'Chrome';
        if (ua.includes('Firefox')) return 'Firefox';
        if (ua.includes('Safari')) return 'Safari';
        if (ua.includes('Edge')) return 'Edge';
        return 'Unknown';
    }

    /**
     * Handle form submission
     */
    async function handleSubmit(e) {
        e.preventDefault();
        
        if (state.isSubmitting) return;
        
        const submitBtn = document.getElementById('feedback-submit-btn');
        const submitText = submitBtn.querySelector('.feedback-submit-text');
        const submitLoading = submitBtn.querySelector('.feedback-submit-loading');
        
        // Validate
        const description = document.getElementById('feedback-description').value.trim();
        if (!description) {
            showToast('error', 'Please enter a description for your feedback.');
            document.getElementById('feedback-description').focus();
            return;
        }
        
        // Get form data
        const feedbackType = document.querySelector('input[name="feedback_type"]:checked').value;
        const subject = document.getElementById('feedback-subject').value.trim();
        const priority = document.getElementById('feedback-priority').value;
        const includeDiagnostics = document.getElementById('feedback-include-diagnostics').checked;
        
        // Build payload
        const payload = {
            feedback_type: feedbackType,
            description: description,
            page_url: window.location.pathname,
            page_title: document.title,
            include_diagnostics: includeDiagnostics
        };
        
        if (subject) {
            payload.subject = subject;
        }
        
        if (feedbackType === 'bug') {
            payload.priority = priority;
        }
        
        if (includeDiagnostics) {
            payload.browser_info = navigator.userAgent;
            payload.screen_resolution = `${window.screen.width}x${window.screen.height}`;
            
            if (state.recentErrors.length > 0) {
                payload.recent_errors = state.recentErrors;
            }
        }
        
        // Submit
        state.isSubmitting = true;
        submitBtn.disabled = true;
        submitText.style.display = 'none';
        submitLoading.style.display = 'inline';
        
        try {
            const response = await fetch(CONFIG.submitEndpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            });
            
            const result = await response.json();
            
            if (result.success) {
                // Reset submitting state BEFORE closing modal
                state.isSubmitting = false;
                submitBtn.disabled = false;
                submitText.style.display = 'inline';
                submitLoading.style.display = 'none';
                
                showToast('success', result.message || 'Thank you for your feedback!');
                closeModal();
                
                // Clear recent errors after successful bug report
                if (feedbackType === 'bug') {
                    state.recentErrors = [];
                }
            } else {
                showToast('error', result.error || 'Failed to submit feedback. Please try again.');
            }
        } catch (error) {
            console.error('Feedback submission error:', error);
            showToast('error', 'Unable to submit feedback. Please check your connection and try again.');
        } finally {
            // Only reset if not already done (i.e., on error)
            if (state.isSubmitting) {
                state.isSubmitting = false;
                submitBtn.disabled = false;
                submitText.style.display = 'inline';
                submitLoading.style.display = 'none';
            }
        }
    }

    /**
     * Reset the form to initial state
     */
    function resetForm() {
        const form = document.getElementById('feedback-form');
        form.reset();
        
        // Reset to general type
        document.querySelector('input[name="feedback_type"][value="general"]').checked = true;
        document.getElementById('feedback-priority-group').style.display = 'none';
        document.getElementById('feedback-diagnostics-preview').style.display = 'none';
        document.getElementById('feedback-char-count').textContent = '0';
        
        // Reset placeholder
        document.getElementById('feedback-description').placeholder = 
            'Share your thoughts, suggestions, or any other feedback...';
    }

    // ===========================================
    // Toast Notifications
    // ===========================================

    /**
     * Show a toast notification
     */
    function showToast(type, message) {
        const toast = document.getElementById('feedback-toast');
        const icon = toast.querySelector('.feedback-toast-icon');
        const messageEl = toast.querySelector('.feedback-toast-message');
        
        // Set content
        messageEl.textContent = message;
        
        // Set icon and color
        toast.className = 'feedback-toast feedback-toast-' + type;
        if (type === 'success') {
            icon.innerHTML = '<i class="fas fa-check-circle"></i>';
        } else {
            icon.innerHTML = '<i class="fas fa-exclamation-circle"></i>';
        }
        
        // Show
        toast.classList.add('show');
        
        // Auto-hide after 5 seconds
        setTimeout(() => {
            hideToast();
        }, 5000);
    }

    /**
     * Hide the toast notification
     */
    function hideToast() {
        const toast = document.getElementById('feedback-toast');
        toast.classList.remove('show');
    }

    // ===========================================
    // Initialization
    // ===========================================

    /**
     * Initialize the feedback widget
     */
    function init() {
        if (state.initialized) return;

        // Don't initialize on login page
        if (window.location.pathname === '/login' || window.location.pathname === '/register') {
            return;
        }

        setupErrorTracking();
        createFeedbackModal();
        setupEventListeners();
        
        state.initialized = true;
        console.log('Feedback widget initialized');
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Expose API for programmatic use
    window.FeedbackWidget = {
        open: openModal,
        close: closeModal,
        getRecentErrors: () => [...state.recentErrors]
    };

})();
