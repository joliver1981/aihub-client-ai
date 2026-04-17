/**
 * Page Context: Agent Email Inbox
 * 
 * Provides real-time context about the agent inbox page state.
 * Add this script to agent_inbox.html
 * 
 * Extracts:
 * - Agent info and email address
 * - Email statistics (new, total)
 * - Current filter
 * - Selected email details
 * - Reply composer state
 */

window.assistantPageContext = {
    page: 'agent_inbox',
    pageName: 'Agent Email Inbox',
    
    getPageData: function() {
        const data = {
            // Agent info
            agent: {
                id: null,
                name: '',
                emailAddress: ''
            },
            
            // Email statistics
            statistics: {
                newCount: 0,
                totalCount: 0
            },
            
            // Current filter
            filter: {
                value: 'all',
                label: 'All'
            },
            
            // Email list
            emailList: {
                count: 0,
                hasEmails: false,
                isLoading: false
            },
            
            // Selected email
            selectedEmail: {
                isSelected: false,
                subject: '',
                from: '',
                to: '',
                date: '',
                hasAttachments: false,
                attachmentCount: 0
            },
            
            // Reply composer
            replyComposer: {
                isOpen: false,
                hasContent: false
            },
            
            // UI state
            uiState: {
                canMarkAllRead: false,
                isDetailView: false
            },
            
            // Available actions
            availableActions: []
        };
        
        // === AGENT INFO ===
        // Get agent ID from global variable if available
        if (typeof agentId !== 'undefined') {
            data.agent.id = agentId;
        }
        
        const agentNameEl = document.getElementById('agentName');
        if (agentNameEl) {
            data.agent.name = agentNameEl.textContent.trim();
        }
        
        const emailAddressEl = document.getElementById('emailAddress');
        if (emailAddressEl) {
            data.agent.emailAddress = emailAddressEl.textContent.trim();
        }
        
        // === STATISTICS ===
        const statNewEl = document.getElementById('statNew');
        if (statNewEl) {
            data.statistics.newCount = parseInt(statNewEl.textContent) || 0;
        }
        
        const statTotalEl = document.getElementById('statTotal');
        if (statTotalEl) {
            data.statistics.totalCount = parseInt(statTotalEl.textContent) || 0;
        }
        
        // === FILTER ===
        const filterSelect = document.getElementById('filterSelect');
        if (filterSelect) {
            data.filter.value = filterSelect.value;
            const selectedOption = filterSelect.options[filterSelect.selectedIndex];
            data.filter.label = selectedOption ? selectedOption.text : 'All';
        }
        
        // === EMAIL LIST ===
        const emailListContainer = document.getElementById('emailListContainer');
        if (emailListContainer) {
            const loadingState = emailListContainer.querySelector('.loading-state');
            data.emailList.isLoading = loadingState && loadingState.style.display !== 'none';
            
            const emailItems = emailListContainer.querySelectorAll('.email-item');
            data.emailList.count = emailItems.length;
            data.emailList.hasEmails = emailItems.length > 0;
        }
        
        // === SELECTED EMAIL ===
        const emailDetailView = document.getElementById('emailDetailView');
        const noSelectionView = document.getElementById('noSelectionView');
        
        if (emailDetailView && emailDetailView.classList.contains('active')) {
            data.selectedEmail.isSelected = true;
            data.uiState.isDetailView = true;
            
            const subjectEl = document.getElementById('detailSubject');
            if (subjectEl) {
                data.selectedEmail.subject = subjectEl.textContent.trim();
            }
            
            const fromEl = document.getElementById('detailFrom');
            if (fromEl) {
                data.selectedEmail.from = fromEl.textContent.trim();
            }
            
            const toEl = document.getElementById('detailTo');
            if (toEl) {
                data.selectedEmail.to = toEl.textContent.trim();
            }
            
            const dateEl = document.getElementById('detailDate');
            if (dateEl) {
                data.selectedEmail.date = dateEl.textContent.trim();
            }
            
            // Check attachments
            const attachmentsSection = document.getElementById('attachmentsSection');
            if (attachmentsSection && attachmentsSection.style.display !== 'none') {
                data.selectedEmail.hasAttachments = true;
                const countEl = document.getElementById('attachmentCount');
                if (countEl) {
                    data.selectedEmail.attachmentCount = parseInt(countEl.textContent) || 0;
                }
            }
        }
        
        // === REPLY COMPOSER ===
        const replyComposer = document.getElementById('replyComposer');
        if (replyComposer && replyComposer.style.display !== 'none') {
            data.replyComposer.isOpen = true;
            
            const replyBody = document.getElementById('replyBody');
            if (replyBody) {
                data.replyComposer.hasContent = replyBody.value.trim().length > 0;
            }
        }
        
        // === UI STATE ===
        const markReadBtn = document.getElementById('markReadBtn');
        if (markReadBtn) {
            data.uiState.canMarkAllRead = !markReadBtn.disabled;
        }
        
        // === AVAILABLE ACTIONS ===
        if (data.emailList.isLoading) {
            data.availableActions = [
                'Waiting for emails to load...'
            ];
        } else if (!data.emailList.hasEmails) {
            data.availableActions = [
                'No emails in inbox',
                'Emails sent to agent will appear here',
                'Refresh to check for new emails',
                'Go to Settings to configure email'
            ];
        } else if (data.replyComposer.isOpen) {
            data.availableActions = [
                'Write your reply message',
                'Click Send Reply to send',
                'Click Cancel to discard'
            ];
        } else if (!data.selectedEmail.isSelected) {
            data.availableActions = [
                'Select an email to view',
                'Use filter to show specific emails',
                'Refresh to check for new emails'
            ];
            if (data.uiState.canMarkAllRead) {
                data.availableActions.push('Mark all emails as read');
            }
        } else {
            data.availableActions = [
                'Read email content',
                'Click Reply to respond',
                'Select another email'
            ];
            if (data.selectedEmail.hasAttachments) {
                data.availableActions.push('Download attachments');
            }
        }
        
        // Debug summary
        console.log('=== Agent Inbox Context ===');
        console.log('Agent:', data.agent.name, '|', data.agent.emailAddress);
        console.log('Stats - New:', data.statistics.newCount, '| Total:', data.statistics.totalCount);
        console.log('Filter:', data.filter.label);
        console.log('Emails in list:', data.emailList.count);
        console.log('Selected:', data.selectedEmail.isSelected ? data.selectedEmail.subject : '(none)');
        console.log('Reply composer open:', data.replyComposer.isOpen);
        
        return data;
    }
};

console.log('Agent Inbox context loaded');
