// feedback.js - Client-side code for the feedback system

/**
 * Feedback component that gets injected after each AI response
 */
function createFeedbackComponent(questionId, agentId, originalQuestion, originalAnswer, confidenceScore) {
    const feedbackHtml = `
    <div class="feedback-container mt-2 mb-4" id="feedback-${questionId}">
        <div class="initial-feedback">
            <small class="text-muted">Was this response helpful?</small>
            <div class="btn-group ml-2" role="group">
                <button type="button" class="btn btn-sm btn-outline-success feedback-btn" 
                        onclick="submitQuickFeedback('${questionId}', 'positive')">
                    <i class="fas fa-thumbs-up"></i>
                </button>
                <button type="button" class="btn btn-sm btn-outline-danger feedback-btn" 
                        onclick="submitQuickFeedback('${questionId}', 'negative')">
                    <i class="fas fa-thumbs-down"></i>
                </button>
            </div>
        </div>
        <div class="detailed-feedback mt-2" id="detailed-feedback-${questionId}" style="display:none;">
            <form id="feedback-form-${questionId}">
                <div class="form-group">
                    <label for="feedback-rating-${questionId}">Rate this response (1-5):</label>
                    <div class="rating">
                        ${[1, 2, 3, 4, 5].map(num => `
                            <input type="radio" id="star${num}-${questionId}" name="rating" value="${num}" />
                            <label for="star${num}-${questionId}"><i class="fas fa-star"></i></label>
                        `).join('')}
                    </div>
                </div>
                <div class="form-group">
                    <label for="feedback-details-${questionId}">What was wrong with this response?</label>
                    <textarea class="form-control" id="feedback-details-${questionId}" rows="2" 
                            placeholder="Please describe the issue..."></textarea>
                </div>
                <button type="button" class="btn btn-primary btn-sm" 
                        onclick="submitDetailedFeedback('${questionId}', '${agentId}', '${encodeURIComponent(originalQuestion)}', '${encodeURIComponent(originalAnswer)}', ${confidenceScore})">
                    Submit Feedback
                </button>
                <button type="button" class="btn btn-link btn-sm" 
                        onclick="cancelFeedback('${questionId}')">
                    Cancel
                </button>
            </form>
        </div>
        <div class="feedback-thanks mt-2" id="feedback-thanks-${questionId}" style="display:none;">
            <div class="alert alert-success py-1">Thank you for your feedback!</div>
        </div>
    </div>
    `;
    
    return feedbackHtml;
}

/**
 * Submit quick feedback (thumbs up/down)
 */
function submitQuickFeedback(questionId, feedbackType) {
    // For negative feedback, show detailed form
    if (feedbackType === 'negative') {
        document.getElementById(`detailed-feedback-${questionId}`).style.display = 'block';
        document.getElementById(`initial-feedback-${questionId}`).style.display = 'none';
        return;
    }
    
    // For positive feedback, submit directly
    const feedbackData = {
        session_id: getSessionId(),
        question_id: questionId,
        agent_id: getCurrentAgentId(),
        original_question: getCurrentQuestion(),
        original_answer: getAnswerText(questionId),
        feedback_type: feedbackType,
        rating: feedbackType === 'positive' ? 5 : 1,
        confidence_score: getConfidenceScore(questionId)
    };
    
    // Submit the feedback
    fetch('/api/feedback', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(feedbackData)
    })
    .then(response => response.json())
    .then(data => {
        // Show thank you message
        document.getElementById(`initial-feedback-${questionId}`).style.display = 'none';
        document.getElementById(`feedback-thanks-${questionId}`).style.display = 'block';
        
        // Log success
        console.log('Feedback submitted successfully:', data);
    })
    .catch(error => {
        console.error('Error submitting feedback:', error);
        alert('There was an error submitting your feedback. Please try again.');
    });
}

/**
 * Submit detailed feedback
 */
function submitDetailedFeedback(questionId, agentId, encodedQuestion, encodedAnswer, confidenceScore) {
    // Get form values
    const rating = document.querySelector(`input[name="rating"]:checked`)?.value || 1;
    const details = document.getElementById(`feedback-details-${questionId}`).value;
    const originalQuestion = decodeURIComponent(encodedQuestion);
    const originalAnswer = decodeURIComponent(encodedAnswer);
    
    // Create feedback data object
    const feedbackData = {
        session_id: getSessionId(),
        question_id: questionId,
        agent_id: agentId,
        original_question: originalQuestion,
        original_answer: originalAnswer,
        feedback_type: 'detailed',
        feedback_details: details,
        rating: parseInt(rating),
        confidence_score: confidenceScore,
        caution_level: getCautionLevel()
    };
    
    // Submit the feedback
    fetch('/api/feedback', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(feedbackData)
    })
    .then(response => response.json())
    .then(data => {
        // Show thank you message
        document.getElementById(`detailed-feedback-${questionId}`).style.display = 'none';
        document.getElementById(`feedback-thanks-${questionId}`).style.display = 'block';
        
        // Log success
        console.log('Detailed feedback submitted successfully:', data);
    })
    .catch(error => {
        console.error('Error submitting feedback:', error);
        alert('There was an error submitting your feedback. Please try again.');
    });
}

/**
 * Cancel feedback submission
 */
function cancelFeedback(questionId) {
    // Reset and hide detailed form
    document.getElementById(`feedback-form-${questionId}`).reset();
    document.getElementById(`detailed-feedback-${questionId}`).style.display = 'none';
    
    // Show initial feedback options again
    document.getElementById(`initial-feedback-${questionId}`).style.display = 'block';
}

// Utility functions
function getSessionId() {
    // Get or create a session ID
    let sessionId = sessionStorage.getItem('sessionId');
    if (!sessionId) {
        sessionId = 'session_' + Math.random().toString(36).substr(2, 9);
        sessionStorage.setItem('sessionId', sessionId);
    }
    return sessionId;
}

function getCurrentAgentId() {
    return document.getElementById('agent_id').value;
}

function getCurrentQuestion() {
    return document.getElementById('user-input').value;
}

function getAnswerText(questionId) {
    // Find the answer element associated with this question
    const messageElement = document.querySelector(`[data-question-id="${questionId}"]`);
    return messageElement ? messageElement.textContent : '';
}

function getConfidenceScore(questionId) {
    // Get confidence score from data attribute (if available)
    const messageElement = document.querySelector(`[data-question-id="${questionId}"]`);
    return messageElement ? parseFloat(messageElement.dataset.confidenceScore || '0') : 0;
}

function getCautionLevel() {
    // Get the current caution level setting
    return document.getElementById('caution-level-setting')?.value || 'medium';
}

// Add CSS for star rating
document.addEventListener('DOMContentLoaded', function() {
    const style = document.createElement('style');
    style.textContent = `
        .rating {
            display: inline-flex;
            flex-direction: row-reverse;
            margin-bottom: 10px;
        }
        
        .rating input {
            display: none;
        }
        
        .rating label {
            cursor: pointer;
            color: #ccc;
            font-size: 1.5rem;
            padding: 0 0.1rem;
        }
        
        .rating input:checked ~ label {
            color: #ffca08;
        }
        
        .rating label:hover,
        .rating label:hover ~ label {
            color: #ffca08;
        }
        
        .feedback-container {
            background-color: #f8f9fa;
            border-radius: 0.5rem;
            padding: 10px;
            margin-left: 40px;
        }
    `;
    document.head.appendChild(style);
});