/**
 * User Preferences Module
 * 
 * Provides client-side management of user preferences with
 * automatic synchronization to the server.
 */
const UserPreferences = (function() {
    let preferences = null;
    let definitions = null;
    let isLoading = false;
    let loadPromise = null;
    
    // Event system for changes
    const events = {};
    
    /**
     * Subscribe to changes in a preference value
     * 
     * @param {string} key - Preference key to watch
     * @param {function} callback - Function to call when value changes
     * @return {function} Unsubscribe function
     */
    function onPreferenceChange(key, callback) {
      if (!events[key]) {
        events[key] = [];
      }
      events[key].push(callback);
      return function unsubscribe() {
        events[key] = events[key].filter(cb => cb !== callback);
      };
    }
    
    /**
     * Trigger change event for a preference
     * 
     * @param {string} key - Preference key that changed
     * @param {*} value - New value
     * @private
     */
    function triggerChange(key, value) {
      if (events[key]) {
        events[key].forEach(callback => callback(value));
      }
    }
    
    /**
     * Load all preferences from server
     * 
     * @return {Promise<Object>} Promise resolving to preferences object
     */
    function loadPreferences() {
      if (isLoading) return loadPromise;
      
      isLoading = true;
      loadPromise = fetch('/preferences/api/get')
        .then(response => {
          if (!response.ok) {
            throw new Error(`Server returned ${response.status}: ${response.statusText}`);
          }
          return response.json();
        })
        .then(data => {
          if (data.status === 'success') {
            preferences = data.preferences || {};
            definitions = data.definitions || {};
            return preferences;
          } else {
            throw new Error(data.message || 'Failed to load preferences');
          }
        })
        .catch(error => {
          console.error('Error loading preferences:', error);
          preferences = {};
          definitions = {};
          throw error;
        })
        .finally(() => {
          isLoading = false;
        });
      
      return loadPromise;
    }
    
    /**
     * Get a preference value with optional fallback
     * 
     * @param {string} key - Preference key
     * @param {*} defaultValue - Fallback value if preference is not set
     * @return {*} Preference value or default
     */
    function getPreference(key, defaultValue) {
      if (preferences === null) {
        console.warn(`Preferences not loaded yet. Returning default for "${key}"`);
        return defaultValue;
      }
      
      return key in preferences ? preferences[key] : defaultValue;
    }
    
    /**
     * Get preference definitions
     * 
     * @return {Object} Preference definitions or null if not loaded
     */
    function getDefinitions() {
      return definitions;
    }
    
    /**
     * Update a preference value and sync to server
     * 
     * @param {string} key - Preference key
     * @param {*} value - New value
     * @return {Promise<boolean>} Promise resolving to success status
     */
    function setPreference(key, value) {
      return fetch('/preferences/api/update', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ key, value }),
      })
      .then(response => {
        if (!response.ok) {
          throw new Error(`Server returned ${response.status}: ${response.statusText}`);
        }
        return response.json();
      })
      .then(data => {
        if (data.status === 'success') {
          // Update local cache
          if (preferences) {
            preferences[key] = value;
          }
          
          // Trigger change event
          triggerChange(key, value);
          return true;
        } else {
          throw new Error(data.message || 'Failed to update preference');
        }
      });
    }
    
    /**
     * Reset all preferences to default values
     * 
     * @return {Promise<boolean>} Promise resolving to success status
     */
    function resetAllPreferences() {
      return fetch('/preferences/api/reset', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        }
      })
      .then(response => {
        if (!response.ok) {
          throw new Error(`Server returned ${response.status}: ${response.statusText}`);
        }
        return response.json();
      })
      .then(data => {
        if (data.status === 'success') {
          // Reload preferences to get defaults
          return loadPreferences().then(() => {
            // Trigger change events for all preferences
            if (preferences && definitions) {
              Object.keys(preferences).forEach(key => {
                triggerChange(key, preferences[key]);
              });
            }
            return true;
          });
        } else {
          throw new Error(data.message || 'Failed to reset preferences');
        }
      });
    }
    
    /**
     * Initialize the preferences module
     * 
     * @return {Promise<Object>} Promise resolving to preferences object
     */
    function init() {
      // Auto-load preferences on initialization
      return loadPreferences();
    }
    
    // Public API
    return {
      init,
      getPreference,
      setPreference,
      onPreferenceChange,
      loadPreferences,
      getDefinitions,
      resetAllPreferences
    };
  })();
  
  // Auto-initialize on DOMContentLoaded
  document.addEventListener('DOMContentLoaded', function() {
    UserPreferences.init().catch(err => {
      console.warn('Failed to initialize preferences:', err);
    });
  });

