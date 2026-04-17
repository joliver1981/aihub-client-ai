/**
 * Page Context: Feedback Analysis Dashboard
 *
 * Provides real-time context about the feedback analysis page state.
 * Add this script to feedback_analysis.html
 *
 * Extracts:
 * - Summary statistics (total, positive, negative, needs review, avg rating)
 * - Selected filters (time period, status, agent, type)
 * - Problematic questions data
 * - Recent feedback with filters
 * - Detail panel state
 */

window.assistantPageContext = {
    page: 'feedback_analysis',
    pageName: 'Feedback Analysis Dashboard',

    getPageData: function() {
        const data = {
            // Filters
            filters: {
                timePeriod: { value: 'all', label: 'All Time' },
                status: 'all',
                agent: 'all',
                type: 'all',
                search: ''
            },

            // Summary statistics
            statistics: {
                totalFeedback: 0,
                positiveFeedback: { count: 0, percent: '' },
                negativeFeedback: { count: 0, percent: '' },
                needsReview: { count: 0, percent: '' },
                averageRating: 0
            },

            // Problematic questions
            problematicQuestions: {
                count: 0,
                questions: []
            },

            // Recent feedback
            recentFeedback: {
                count: 0,
                currentPage: 1,
                entries: []
            },

            // Detail panel state
            detailPanel: {
                isOpen: false,
                feedbackId: null
            },

            // Available actions
            availableActions: [],

            // Data quality indicators
            dataQuality: {
                hasData: false,
                hasProblems: false
            }
        };

        // === FILTERS ===
        const timePeriodSelect = document.getElementById('time-period-selector');
        if (timePeriodSelect) {
            data.filters.timePeriod.value = timePeriodSelect.value;
            const selectedOption = timePeriodSelect.options[timePeriodSelect.selectedIndex];
            data.filters.timePeriod.label = selectedOption ? selectedOption.text : '';
        }

        const statusFilter = document.getElementById('status-filter');
        if (statusFilter) data.filters.status = statusFilter.value;

        const agentFilter = document.getElementById('agent-filter');
        if (agentFilter) data.filters.agent = agentFilter.value;

        const typeFilter = document.getElementById('feedback-type-filter');
        if (typeFilter) data.filters.type = typeFilter.value;

        const searchInput = document.getElementById('feedback-search');
        if (searchInput) data.filters.search = searchInput.value.trim();

        // === SUMMARY STATISTICS ===
        const totalFeedbackEl = document.getElementById('total-feedback-count');
        const positiveFeedbackEl = document.getElementById('positive-feedback-count');
        const positivePercentEl = document.getElementById('positive-feedback-percent');
        const negativeFeedbackEl = document.getElementById('negative-feedback-count');
        const negativePercentEl = document.getElementById('negative-feedback-percent');
        const needsReviewEl = document.getElementById('needs-review-count');
        const needsReviewPercentEl = document.getElementById('needs-review-percent');
        const averageRatingEl = document.getElementById('average-rating');

        if (totalFeedbackEl && totalFeedbackEl.textContent !== '-') {
            data.statistics.totalFeedback = parseInt(totalFeedbackEl.textContent) || 0;
            data.dataQuality.hasData = data.statistics.totalFeedback > 0;
        }

        if (positiveFeedbackEl && positiveFeedbackEl.textContent !== '-') {
            data.statistics.positiveFeedback.count = parseInt(positiveFeedbackEl.textContent) || 0;
        }
        if (positivePercentEl) {
            data.statistics.positiveFeedback.percent = positivePercentEl.textContent.trim();
        }

        if (negativeFeedbackEl && negativeFeedbackEl.textContent !== '-') {
            data.statistics.negativeFeedback.count = parseInt(negativeFeedbackEl.textContent) || 0;
        }
        if (negativePercentEl) {
            data.statistics.negativeFeedback.percent = negativePercentEl.textContent.trim();
        }

        if (needsReviewEl && needsReviewEl.textContent !== '-') {
            data.statistics.needsReview.count = parseInt(needsReviewEl.textContent) || 0;
        }
        if (needsReviewPercentEl) {
            data.statistics.needsReview.percent = needsReviewPercentEl.textContent.trim();
        }

        if (averageRatingEl && averageRatingEl.textContent !== '-') {
            data.statistics.averageRating = parseFloat(averageRatingEl.textContent) || 0;
        }

        // === PROBLEMATIC QUESTIONS ===
        const problematicTable = document.getElementById('problematic-questions-table');
        if (problematicTable) {
            const rows = problematicTable.querySelectorAll('tbody tr');
            data.problematicQuestions.count = rows.length;
            data.dataQuality.hasProblems = rows.length > 0;

            rows.forEach(function(row, index) {
                if (index < 5) {
                    const cells = row.querySelectorAll('td');
                    if (cells.length >= 4) {
                        data.problematicQuestions.questions.push({
                            question: cells[0].textContent.trim().substring(0, 50) +
                                     (cells[0].textContent.length > 50 ? '...' : ''),
                            feedbackCount: cells[1].textContent.trim(),
                            avgRating: cells[2].textContent.trim(),
                            avgConfidence: cells[3].textContent.trim()
                        });
                    }
                }
            });
        }

        // === RECENT FEEDBACK ===
        const recentFeedbackTable = document.getElementById('recent-feedback-table');
        if (recentFeedbackTable) {
            const rows = recentFeedbackTable.querySelectorAll('tbody tr');
            data.recentFeedback.count = rows.length;

            rows.forEach(function(row, index) {
                if (index < 5) {
                    const cells = row.querySelectorAll('td');
                    if (cells.length >= 8) {
                        const typeBadge = cells[4].querySelector('.badge');
                        const statusBadge = cells[5].querySelector('.badge');

                        data.recentFeedback.entries.push({
                            date: cells[0].textContent.trim(),
                            user: cells[1].textContent.trim(),
                            agent: cells[2].textContent.trim(),
                            question: cells[3].textContent.trim().substring(0, 40) +
                                     (cells[3].textContent.length > 40 ? '...' : ''),
                            type: typeBadge ? typeBadge.textContent.trim() : '-',
                            status: statusBadge ? statusBadge.textContent.trim() : '-',
                            confidence: cells[7].textContent.trim()
                        });
                    }
                }
            });
        }

        // === PAGINATION ===
        const activePage = document.querySelector('#feedback-pagination .page-item.active .page-link');
        if (activePage) {
            data.recentFeedback.currentPage = parseInt(activePage.textContent) || 1;
        }

        // === DETAIL PANEL STATE ===
        const panel = document.getElementById('feedback-detail-panel');
        if (panel && panel.classList.contains('open')) {
            data.detailPanel.isOpen = true;
            data.detailPanel.feedbackId = typeof currentFeedbackId !== 'undefined' ? currentFeedbackId : null;
        }

        // === AVAILABLE ACTIONS ===
        if (!data.dataQuality.hasData) {
            data.availableActions = [
                'No feedback data available yet',
                'Users can submit feedback from the Assistants page',
                'Change time period to see more data'
            ];
        } else {
            data.availableActions = [
                'Change time period, status, or agent filter',
                'Review problematic questions',
                'View feedback details and update status',
                'Export feedback data as CSV',
                'Filter by feedback type',
                'Search for specific feedback'
            ];

            if (data.statistics.needsReview.count > 0) {
                data.availableActions.unshift('Review ' + data.statistics.needsReview.count + ' feedback item(s) needing review');
            }

            if (data.dataQuality.hasProblems) {
                data.availableActions.unshift('Address ' + data.problematicQuestions.count + ' problematic question(s)');
            }
        }

        return data;
    }
};

console.log('Feedback Analysis context loaded');
