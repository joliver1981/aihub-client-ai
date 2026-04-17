/**
 * Page Context: Local Secrets
 * 
 * Add this script block to local_secrets.html before the closing </script> tag
 * or in a separate file included after the main page script.
 * 
 * This provides the AI assistant with comprehensive real-time context about:
 * - Stored secrets (names only, never values)
 * - Categories and filtering
 * - Storage information
 * - Modal states
 * - Available actions
 */

window.assistantPageContext = {
    page: 'local_secrets',
    pageName: 'Local Secrets',
    
    getPageData: function() {
        const data = {
            // Secrets overview (NEVER include actual secret values)
            secrets: {
                total: typeof allSecrets !== 'undefined' ? allSecrets.length : 0,
                byCategory: {
                    api_keys: 0,
                    credentials: 0,
                    database: 0,
                    other: 0
                },
                list: [] // Names and categories only, never values
            },
            
            // Current filter
            filter: {
                category: $('#categoryFilter').val() || 'all'
            },
            
            // Storage information
            storageInfo: {
                location: $('#storageLocation').text().trim() || 'Unknown',
                encryption: $('#storageEncryption').text().trim() || 'Unknown',
                cloudSync: $('#storageCloudSync').text().trim() || 'Disabled',
                count: $('#storageCount').text().trim() || '0'
            },
            
            // Modal states
            modals: {
                addEditOpen: $('#secretModal').hasClass('show') || $('#secretModal').is(':visible'),
                deleteConfirmOpen: $('#deleteModal').hasClass('show') || $('#deleteModal').is(':visible'),
                importOpen: $('#importModal').hasClass('show') || $('#importModal').is(':visible'),
                mode: $('#secretMode').val() || 'add' // 'add' or 'edit'
            },
            
            // Form state (when modal is open)
            formState: {
                secretName: $('#secretName').val() || '',
                hasValue: !!$('#secretValue').val(),
                description: $('#secretDescription').val() || '',
                category: $('#secretCategory').val() || 'api_keys',
                isEditing: $('#secretName').prop('disabled') || false
            },
            
            // Available actions
            availableActions: [],
            
            // Security reminders
            securityInfo: {
                isLocalOnly: true,
                isEncrypted: true,
                neverUploaded: true
            }
        };
        
        // Get secrets list (names and categories only - NEVER values)
        if (typeof allSecrets !== 'undefined' && Array.isArray(allSecrets)) {
            data.secrets.list = allSecrets.map(secret => ({
                name: secret.name,
                category: secret.category || 'other',
                hasDescription: !!secret.description,
                descriptionPreview: secret.description ? 
                    secret.description.substring(0, 50) + (secret.description.length > 50 ? '...' : '') : null
            }));
            
            // Count by category
            allSecrets.forEach(secret => {
                const cat = secret.category || 'other';
                if (data.secrets.byCategory.hasOwnProperty(cat)) {
                    data.secrets.byCategory[cat]++;
                } else {
                    data.secrets.byCategory.other++;
                }
            });
        } else {
            // Try to extract from DOM
            $('.secrets-table tbody tr').each(function() {
                const name = $(this).find('.secret-name').text().trim();
                const category = $(this).find('.secret-category .badge').text().trim().toLowerCase();
                if (name) {
                    data.secrets.list.push({
                        name: name,
                        category: category || 'other'
                    });
                }
            });
            data.secrets.total = data.secrets.list.length;
        }
        
        // Determine available actions based on state
        if (data.modals.addEditOpen) {
            if (data.formState.isEditing) {
                data.availableActions.push('Update secret value');
                data.availableActions.push('Update description');
                data.availableActions.push('Change category');
                data.availableActions.push('Cancel editing');
            } else {
                data.availableActions.push('Enter secret name');
                data.availableActions.push('Enter secret value');
                data.availableActions.push('Add description (optional)');
                data.availableActions.push('Select category');
                data.availableActions.push('Save secret');
            }
        } else if (data.modals.importOpen) {
            data.availableActions.push('Paste JSON data');
            data.availableActions.push('Choose overwrite option');
            data.availableActions.push('Import secrets');
        } else {
            data.availableActions.push('Add new secret');
            if (data.secrets.total > 0) {
                data.availableActions.push('Edit existing secret');
                data.availableActions.push('Delete secret');
                data.availableActions.push('Test secret');
                data.availableActions.push('Export template');
                data.availableActions.push('Import secrets');
                data.availableActions.push('Filter by category');
            }
        }
        
        // Common secret types for suggestions
        data.commonSecretTypes = [
            { name: 'OPENAI_API_KEY', category: 'api_keys', description: 'OpenAI API key for GPT models' },
            { name: 'ANTHROPIC_API_KEY', category: 'api_keys', description: 'Anthropic API key for Claude' },
            { name: 'AZURE_OPENAI_KEY', category: 'api_keys', description: 'Azure OpenAI service key' },
            { name: 'SENDGRID_API_KEY', category: 'api_keys', description: 'SendGrid email API key' },
            { name: 'SLACK_BOT_TOKEN', category: 'api_keys', description: 'Slack bot OAuth token' },
            { name: 'DATABASE_PASSWORD', category: 'database', description: 'Database connection password' },
            { name: 'SMTP_PASSWORD', category: 'credentials', description: 'SMTP email server password' }
        ];
        
        // Check which common secrets are already configured
        const existingNames = data.secrets.list.map(s => s.name.toUpperCase());
        data.commonSecretTypes.forEach(type => {
            type.isConfigured = existingNames.includes(type.name.toUpperCase());
        });
        
        data.suggestedSecrets = data.commonSecretTypes.filter(t => !t.isConfigured);
        
        return data;
    }
};

// Log that context is loaded
console.log('Local Secrets assistant context loaded');
