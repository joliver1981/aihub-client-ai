// caution-level.js - Client-side code for managing caution levels

/**
 * Initialize caution level features when the DOM is loaded
 */
document.addEventListener('DOMContentLoaded', function() {
    // Check if caution system is enabled first
    checkCautionSystemEnabled();
    
    // Initialize range sliders to update their value display
    initializeRangeSliders();
    
    // Load caution levels from server if available
    const cautionLevelSetting = document.getElementById('caution-level-setting');
    if (cautionLevelSetting) {
        loadCautionLevels();
        
        // Add event listener for caution level setting change
        cautionLevelSetting.addEventListener('change', function() {
            setUserCautionLevel(this.value);
        });
        
        // Load user's caution level setting
        loadUserCautionLevel();
    }
    
    // Add event listeners for save buttons (only if caution UI exists)
    const saveCautionButtons = document.querySelectorAll('.save-caution-level');
    if (saveCautionButtons.length > 0) {
        saveCautionButtons.forEach(button => {
            button.addEventListener('click', function() {
                const levelName = this.dataset.level;
                saveCautionLevel(levelName);
            });
        });
    }
    
    // Save custom level (only if the button exists)
    const saveCustomLevelButton = document.getElementById('save-custom-level');
    if (saveCustomLevelButton) {
        saveCustomLevelButton.addEventListener('click', saveCustomCautionLevel);
    }
});

/**
 * Initialize range sliders to update their value display
 */
function initializeRangeSliders() {
    const rangeSliders = document.querySelectorAll('.custom-range');
    
    rangeSliders.forEach(slider => {
        // Get the value display element
        const valueDisplayId = slider.id + '-value';
        const valueDisplay = document.getElementById(valueDisplayId);
        
        if (valueDisplay) {
            // Set initial value
            valueDisplay.textContent = slider.value;
            
            // Update value on input
            slider.addEventListener('input', function() {
                valueDisplay.textContent = this.value;
            });
        }
    });
}

/**
 * Load caution levels from server
 */
function loadCautionLevels() {
    fetch('/api/caution/levels')
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                populateCautionLevels(data.levels);
            } else {
                showMessage('Error loading caution levels', 'error');
            }
        })
        .catch(error => {
            console.error('Error loading caution levels:', error);
            showMessage('Error loading caution levels', 'error');
        });
}

/**
 * Populate caution level forms with data from server
 */
function populateCautionLevels(levels) {
    // Loop through each level
    for (const [levelName, settings] of Object.entries(levels)) {
        const form = document.getElementById(`${levelName}-form`);
        if (!form) continue;
        
        // Set form values
        Object.entries(settings).forEach(([key, value]) => {
            const element = form.querySelector(`[name="${key}"]`);
            if (!element) return;
            
            if (element.type === 'checkbox') {
                element.checked = value;
            } else {
                element.value = value;
                
                // Update range slider display
                if (element.type === 'range') {
                    const valueDisplayId = element.id + '-value';
                    const valueDisplay = document.getElementById(valueDisplayId);
                    if (valueDisplay) {
                        valueDisplay.textContent = value;
                    }
                }
            }
        });
    }
}

/**
 * Save caution level settings
 */
function saveCautionLevel(levelName) {
    const form = document.getElementById(`${levelName}-form`);
    if (!form) return;
    
    // Get form data
    const formData = new FormData(form);
    const settings = {
        description: formData.get('description'),
        confidence_threshold: parseFloat(formData.get('confidence_threshold')),
        clarification_threshold: parseFloat(formData.get('clarification_threshold')),
        max_assumption_count: parseInt(formData.get('max_assumption_count')),
        allow_extrapolation: formData.get('allow_extrapolation') === 'on'
    };
    
    // Send to server
    fetch('/api/caution/level', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            level_name: levelName,
            settings: settings
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            showMessage(`Caution level "${levelName}" saved successfully`, 'success');
        } else {
            showMessage(`Error saving caution level: ${data.message}`, 'error');
        }
    })
    .catch(error => {
        console.error('Error saving caution level:', error);
        showMessage('Error saving caution level', 'error');
    });
}

/**
 * Save custom caution level
 */
function saveCustomCautionLevel() {
    const form = document.getElementById('custom-form');
    if (!form) return;
    
    // Get form data
    const formData = new FormData(form);
    const levelName = formData.get('level_name');
    
    // Validate level name
    if (!levelName) {
        showMessage('Please enter a name for your custom caution level', 'error');
        return;
    }
    
    const settings = {
        description: formData.get('description'),
        confidence_threshold: parseFloat(formData.get('confidence_threshold')),
        clarification_threshold: parseFloat(formData.get('clarification_threshold')),
        max_assumption_count: parseInt(formData.get('max_assumption_count')),
        allow_extrapolation: formData.get('allow_extrapolation') === 'on'
    };
    
    // Send to server
    fetch('/api/caution/level', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            level_name: levelName,
            settings: settings
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            showMessage(`Custom caution level "${levelName}" created successfully`, 'success');
            
            // Reload caution levels
            loadCautionLevels();
            
            // Clear the form
            form.reset();
        } else {
            showMessage(`Error creating custom caution level: ${data.message}`, 'error');
        }
    })
    .catch(error => {
        console.error('Error creating custom caution level:', error);
        showMessage('Error creating custom caution level', 'error');
    });
}

/**
 * Load user's caution level setting
 */
function loadUserCautionLevel() {
    const userId = getUserId();
    if (!userId) return;
    
    fetch(`/api/caution/user?user_id=${userId}`)
        .then(response => {
            // Check if response is ok before trying to parse JSON
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.status === 'success') {
                // Set the caution level selector
                const selector = document.getElementById('caution-level-setting');
                if (selector) {
                    selector.value = data.level_name;
                }
            } else if (data.status === 'disabled') {
                // System is disabled, hide UI elements
                hideAllCautionUIElements();
                console.log('Caution system is disabled via configuration');
            }
        })
        .catch(error => {
            console.error('Error loading user caution level:', error);
            // Hide caution UI elements if we get an error
            hideAllCautionUIElements();
        });
}

/**
 * Set user's caution level
 */
function setUserCautionLevel(levelName) {
    const userId = getUserId();
    if (!userId) return;
    
    fetch('/api/caution/user', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            user_id: userId,
            level_name: levelName
        })
    })
    .then(response => {
        // Check if response is ok before trying to parse JSON
        if (!response.ok) {
            throw new Error(`HTTP error! Status: ${response.status}`);
        }
        return response.json();
    })
    .then(data => {
        if (data.status === 'success') {
            showMessage(`Caution level set to "${levelName}"`, 'success');
        } else if (data.status === 'disabled') {
            // System is disabled, hide UI elements
            hideAllCautionUIElements();
            console.log('Caution system is disabled via configuration');
        }
    })
    .catch(error => {
        console.error('Error setting user caution level:', error);
        // Don't show an error message to the user, just log it
    });
}

/**
 * Show a message to the user
 */
function showMessage(message, type = 'info') {
    // Create message element
    const messageDiv = document.createElement('div');
    messageDiv.className = `alert alert-${type === 'error' ? 'danger' : 'success'} alert-dismissible fade show`;
    messageDiv.innerHTML = `
        ${message}
        <button type="button" class="close" data-dismiss="alert" aria-label="Close">
            <span aria-hidden="true">&times;</span>
        </button>
    `;
    
    // Add to page
    const container = document.querySelector('.container');
    if (container) {
        container.insertBefore(messageDiv, container.firstChild);
    } else {
        document.body.insertBefore(messageDiv, document.body.firstChild);
    }
    
    // Auto-dismiss after 5 seconds
    setTimeout(() => {
        messageDiv.classList.remove('show');
        setTimeout(() => messageDiv.remove(), 300);
    }, 5000);
}

/**
 * Get current user ID
 */
function getUserId() {
    // Try to get from hidden input
    return document.getElementById('user_id')?.value || null;
}

/**
 * Check if caution system is enabled and handle UI accordingly
 */
function checkCautionSystemEnabled() {
    // Make an API call to check if caution system is enabled
    fetch('/api/system/config?param=caution_system')
        .then(response => {
            // Check if response is ok before trying to parse JSON
            if (!response.ok) {
                throw new Error(`HTTP error! Status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (!data.enabled) {
                // Hide caution UI elements
                hideAllCautionUIElements();
            }
        })
        .catch(error => {
            console.error('Error checking caution system status:', error);
            // On error, assume it's disabled and hide the UI
            hideAllCautionUIElements();
        });
}


/**
 * Hide all caution system UI elements when the system is disabled
 */
function hideAllCautionUIElements() {
    // Hide all elements with caution-system-ui class
    document.querySelectorAll('.caution-system-ui').forEach(element => {
        element.style.display = 'none';
    });
    
    // Also hide the selector if it exists
    const selector = document.getElementById('caution-level-setting');
    if (selector) {
        const container = selector.closest('.card');
        if (container) {
            container.style.display = 'none';
        } else {
            selector.style.display = 'none';
        }
    }
}