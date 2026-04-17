// feedback-pagination.js - Client-side pagination for feedback tables

/**
 * Initialize pagination for the feedback table
 */
function initializePagination(totalPages, currentPage) {
    const paginationElement = document.getElementById('feedback-pagination');
    if (!paginationElement) return;
    
    // Clear existing pagination
    paginationElement.innerHTML = '';
    
    // Create pagination container
    const paginationNav = document.createElement('nav');
    paginationNav.setAttribute('aria-label', 'Feedback pagination');
    
    const paginationList = document.createElement('ul');
    paginationList.className = 'pagination justify-content-center';
    
    // Previous button
    const prevItem = document.createElement('li');
    prevItem.className = `page-item ${currentPage <= 1 ? 'disabled' : ''}`;
    
    const prevLink = document.createElement('a');
    prevLink.className = 'page-link';
    prevLink.href = '#';
    prevLink.textContent = 'Previous';
    
    if (currentPage > 1) {
        prevLink.addEventListener('click', (e) => {
            e.preventDefault();
            loadFeedbackPage(currentPage - 1);
        });
    } else {
        prevLink.setAttribute('tabindex', '-1');
        prevLink.setAttribute('aria-disabled', 'true');
    }
    
    prevItem.appendChild(prevLink);
    paginationList.appendChild(prevItem);
    
    // Page numbers
    let startPage = Math.max(1, currentPage - 2);
    let endPage = Math.min(totalPages, startPage + 4);
    
    // Adjust if we're near the end
    if (endPage - startPage < 4 && startPage > 1) {
        startPage = Math.max(1, endPage - 4);
    }
    
    for (let i = startPage; i <= endPage; i++) {
        const pageItem = document.createElement('li');
        pageItem.className = `page-item ${i === currentPage ? 'active' : ''}`;
        
        const pageLink = document.createElement('a');
        pageLink.className = 'page-link';
        pageLink.href = '#';
        pageLink.textContent = i;
        
        if (i === currentPage) {
            pageLink.setAttribute('aria-current', 'page');
        } else {
            pageLink.addEventListener('click', (e) => {
                e.preventDefault();
                loadFeedbackPage(i);
            });
        }
        
        pageItem.appendChild(pageLink);
        paginationList.appendChild(pageItem);
    }
    
    // Next button
    const nextItem = document.createElement('li');
    nextItem.className = `page-item ${currentPage >= totalPages ? 'disabled' : ''}`;
    
    const nextLink = document.createElement('a');
    nextLink.className = 'page-link';
    nextLink.href = '#';
    nextLink.textContent = 'Next';
    
    if (currentPage < totalPages) {
        nextLink.addEventListener('click', (e) => {
            e.preventDefault();
            loadFeedbackPage(currentPage + 1);
        });
    } else {
        nextLink.setAttribute('tabindex', '-1');
        nextLink.setAttribute('aria-disabled', 'true');
    }
    
    nextItem.appendChild(nextLink);
    paginationList.appendChild(nextItem);
    
    // Append to pagination container
    paginationNav.appendChild(paginationList);
    paginationElement.appendChild(paginationNav);
}

/**
 * Load a specific page of feedback data
 */
function loadFeedbackPage(page) {
    const feedbackType = document.getElementById('feedback-type-filter').value;
    const searchTerm = document.getElementById('feedback-search').value;
    
    // Create URL with query parameters
    let url = `/api/feedback?page=${page}`;
    if (feedbackType !== 'all') {
        url += `&type=${feedbackType}`;
    }
    if (searchTerm) {
        url += `&search=${encodeURIComponent(searchTerm)}`;
    }
    
    // Show loading indicator
    const tableBody = document.getElementById('recent-feedback-table').querySelector('tbody');
    tableBody.innerHTML = '<tr><td colspan="9" class="text-center"><div class="spinner-border text-primary" role="status"><span class="sr-only">Loading...</span></div></td></tr>';
    
    fetch(url)
        .then(response => response.json())
        .then(data => {
            populateFeedbackTable(data.feedback || []);
            
            // Update pagination
            if (data.pagination) {
                initializePagination(data.pagination.total_pages, data.pagination.page);
            }
        })
        .catch(error => {
            console.error('Error loading feedback page:', error);
            tableBody.innerHTML = '<tr><td colspan="9" class="text-center text-danger">Error loading feedback data</td></tr>';
        });
}

/**
 * Add event listener for feedback search and filter changes
 */
function setupFeedbackSearchListeners() {
    const searchInput = document.getElementById('feedback-search');
    const typeFilter = document.getElementById('feedback-type-filter');
    
    // Add debounce to avoid too many requests
    let searchTimeout;
    
    searchInput.addEventListener('input', () => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            loadFeedbackPage(1); // Reset to page 1 when search changes
        }, 300);
    });
    
    typeFilter.addEventListener('change', () => {
        loadFeedbackPage(1); // Reset to page 1 when filter changes
    });
}

// Initialize when document is ready
document.addEventListener('DOMContentLoaded', function() {
    setupFeedbackSearchListeners();
});