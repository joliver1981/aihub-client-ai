/**
 * Page Context: Custom Agent (Enhanced)
 * 
 * Provides real-time context about the agent configuration page.
 * Add this script to custom_agent_enhanced.html
 */

window.assistantPageContext = {
    page: 'custom_agent_enhanced',
    pageName: 'Custom Agent Builder',
    
    getPageData: function() {
        const data = {
            agent: {
                id: null,
                name: '',
                description: '',
                systemPrompt: '',
                isNew: true
            },
            agentsList: {
                total: 0,
                selected: null,
                selectedName: 'None'
            },
            tools: {
                categories: [],
                totalAvailable: 0,
                totalSelected: 0,
                selectedTools: [],
                mandatoryTools: []
            },
            customTools: {
                total: 0,
                selected: 0,
                list: []
            },
            dependencyGroups: {
                available: [],
                selected: []
            },
            knowledge: {
                total: 0,
                items: []
            },
            validation: {
                isValid: true,
                errors: []
            },
            availableActions: []
        };
        
        // === AGENT INFO ===
        // Try multiple possible field IDs for agent name
        data.agent.name = document.getElementById('agentName')?.value || 
                          document.getElementById('agent_name')?.value || 
                          document.getElementById('name')?.value ||
                          document.querySelector('input[name="agent_name"]')?.value || '';
        
        data.agent.description = document.getElementById('agentDescription')?.value || 
                                  document.getElementById('agent_description')?.value || 
                                  document.getElementById('objective')?.value ||
                                  document.querySelector('textarea[name="description"]')?.value || '';
        
        data.agent.systemPrompt = document.getElementById('systemPrompt')?.value || '';
        
        // Agent selector - this is the key dropdown (id="agent-dropdown" in HTML)
        const agentSelect = document.getElementById('agent-dropdown');
        if (agentSelect && agentSelect.value) {
            data.agentsList.selected = agentSelect.value;
            const selectedOption = agentSelect.options[agentSelect.selectedIndex];
            data.agentsList.selectedName = selectedOption ? selectedOption.text.trim() : 'Unknown';
            data.agentsList.total = agentSelect.options.length - 1;
            data.agent.isNew = agentSelect.value === '' || agentSelect.value === 'new';
            
            // Use dropdown text as agent name if name field is empty
            if (!data.agent.name && selectedOption) {
                data.agent.name = selectedOption.text.trim();
            }
        }
        
        // Hidden agent_id field
        const agentIdField = document.getElementById('agent_id');
        if (agentIdField && agentIdField.value) {
            data.agent.id = agentIdField.value;
            data.agent.isNew = false;
        }
        
        // Check for currentAgent JS variable (set by page when agent loads)
        if (typeof currentAgent !== 'undefined' && currentAgent) {
            data.agent.id = currentAgent.id || data.agent.id;
            if (!data.agent.name) {
                data.agent.name = currentAgent.name || currentAgent.agent_name || '';
            }
            data.agent.isNew = !currentAgent.id;
        }
        
        // Debug agent detection
        console.log('Agent select value:', agentSelect?.value);
        console.log('Agent select text:', agentSelect?.options[agentSelect?.selectedIndex]?.text);
        console.log('Detected agent name:', data.agent.name);
        
        // === CORE TOOLS (from #core-tool-categories) ===
        // These are dynamically rendered into category divs
        // IMPORTANT: Tools exist in multiple views, so we must deduplicate
        const coreToolContainer = document.getElementById('core-tool-categories');
        const categoryMap = {};
        const seenTools = new Set(); // Track unique tools to avoid duplicates
        
        // Debug logging
        console.log('=== Assistant Context Debug ===');
        console.log('Container #core-tool-categories exists:', !!coreToolContainer);
        
        // Only get checkboxes from the CATEGORY view (the visible one)
        // This avoids duplicates from the hidden "all tools" view
        const coreToolCheckboxes = document.querySelectorAll('#core-tool-categories input[name="core-tools"]');
        console.log('Core tool checkboxes in category view:', coreToolCheckboxes.length);
        
        coreToolCheckboxes.forEach(function(checkbox) {
            const toolName = checkbox.value;
            
            // Skip if we've already processed this tool (deduplication)
            if (seenTools.has(toolName)) {
                return;
            }
            seenTools.add(toolName);
            
            const isChecked = checkbox.checked;
            const isDisabled = checkbox.disabled;
            
            // Get the display name and description from the label structure
            // HTML: <label><input><span class="tool-info"><strong>Display Name:</strong> <span class="text-muted">Description</span></span></label>
            const label = checkbox.closest('label');
            const toolInfo = label ? label.querySelector('.tool-info') : null;
            const strongEl = toolInfo ? toolInfo.querySelector('strong') : null;
            const descEl = toolInfo ? toolInfo.querySelector('.text-muted') : null;
            
            let displayName = toolName;
            let description = '';
            
            if (strongEl) {
                displayName = strongEl.textContent.replace(/:$/, '').trim();
            }
            if (descEl) {
                description = descEl.textContent.trim();
            }
            
            // Debug: log checked tools with full info
            if (isChecked) {
                console.log('Found CHECKED core tool:', displayName, '(' + toolName + ')', '-', description.substring(0, 50) + '...');
            }
            
            // Get category from parent .tool-category
            const categoryDiv = checkbox.closest('.tool-category');
            let categoryName = 'Core Tools';
            if (categoryDiv) {
                const headerEl = categoryDiv.querySelector('.category-header');
                if (headerEl) {
                    // Clone to avoid modifying original, remove badges/icons
                    const clone = headerEl.cloneNode(true);
                    const badge = clone.querySelector('.badge');
                    const icon = clone.querySelector('i');
                    if (badge) badge.remove();
                    if (icon) icon.remove();
                    categoryName = clone.textContent.trim();
                }
            }
            
            // Track category stats
            if (!categoryMap[categoryName]) {
                categoryMap[categoryName] = { name: categoryName, total: 0, selected: 0 };
            }
            categoryMap[categoryName].total++;
            
            data.tools.totalAvailable++;
            
            if (isChecked) {
                categoryMap[categoryName].selected++;
                data.tools.totalSelected++;
                data.tools.selectedTools.push({
                    name: displayName,
                    toolId: toolName,
                    description: description,
                    category: categoryName,
                    isMandatory: isDisabled
                });
                
                if (isDisabled) {
                    data.tools.mandatoryTools.push(displayName);
                }
            }
        });
        
        data.tools.categories = Object.values(categoryMap);
        
        // === CUSTOM TOOLS (from #tool-list) ===
        const customToolCheckboxes = document.querySelectorAll('#tool-list input[name="files"]');
        const seenCustomTools = new Set();
        
        customToolCheckboxes.forEach(function(checkbox) {
            const toolName = checkbox.value;
            
            // Skip duplicates
            if (seenCustomTools.has(toolName)) {
                return;
            }
            seenCustomTools.add(toolName);
            
            const isChecked = checkbox.checked;
            
            // Get display name and description
            const label = checkbox.closest('label');
            const toolInfo = label ? label.querySelector('.tool-info') : null;
            const strongEl = toolInfo ? toolInfo.querySelector('strong') : null;
            const descEl = toolInfo ? toolInfo.querySelector('.text-muted') : null;
            
            let displayName = toolName;
            let description = '';
            
            if (strongEl) {
                displayName = strongEl.textContent.replace(/:$/, '').trim();
            }
            if (descEl) {
                description = descEl.textContent.trim();
            }
            
            data.customTools.total++;
            data.customTools.list.push({
                name: displayName,
                toolId: toolName,
                description: description,
                isSelected: isChecked
            });
            
            if (isChecked) {
                data.customTools.selected++;
                data.tools.totalSelected++;
                data.tools.selectedTools.push({
                    name: displayName,
                    toolId: toolName,
                    description: description,
                    category: 'Custom Tools',
                    isMandatory: false
                });
            }
        });
        
        // === DEPENDENCY GROUPS ===
        const groupCards = document.querySelectorAll('.dependency-group-card');
        groupCards.forEach(function(card) {
            const titleEl = card.querySelector('.card-title, h6');
            const groupName = titleEl ? titleEl.textContent.trim() : '';
            const isSelected = card.classList.contains('selected');
            
            if (groupName) {
                data.dependencyGroups.available.push({
                    name: groupName,
                    isSelected: isSelected
                });
                if (isSelected) {
                    data.dependencyGroups.selected.push(groupName);
                }
            }
        });
        
        // === KNOWLEDGE ===
        if (typeof agentKnowledge !== 'undefined' && Array.isArray(agentKnowledge)) {
            data.knowledge.total = agentKnowledge.length;
            data.knowledge.items = agentKnowledge.map(function(k) {
                return { name: k.name || k.title, type: k.type || 'document' };
            });
        }
        
        // === VALIDATION ===
        if (!data.agent.name && !data.agent.isNew) {
            data.validation.isValid = false;
            data.validation.errors.push('Agent name is required');
        }
        if (data.tools.totalSelected === 0) {
            data.validation.isValid = false;
            data.validation.errors.push('At least one tool must be selected');
        }
        
        // === DIALOG STATE ===
        const addAgentPopup = document.getElementById('add-agent-popup');
        const importDialog = document.getElementById('importAgentModal');
        
        data.dialogState = {
            addAgentOpen: addAgentPopup && addAgentPopup.style.display !== 'none' && addAgentPopup.style.display !== '',
            importOpen: importDialog && importDialog.classList.contains('show')
        };
        
        // === AVAILABLE ACTIONS ===
        if (data.dialogState.addAgentOpen) {
            data.availableActions = [
                'Enter name for new agent',
                'Write agent objective',
                'Select core tools',
                'Select custom tools',
                'Save the new agent',
                'Cancel to close dialog'
            ];
        } else if (data.dialogState.importOpen) {
            data.availableActions = [
                'Select agent package file',
                'Analyze package contents',
                'Review conflicts if any',
                'Proceed with import',
                'Cancel to close dialog'
            ];
        } else if (!data.agentsList.selected || data.agentsList.selected === '') {
            data.availableActions = [
                'Select an agent from the dropdown',
                'Add a new agent',
                'Import an existing agent'
            ];
        } else {
            data.availableActions = [
                'Modify agent objective',
                'Change agent name',
                'Add or remove core tools',
                'Add or remove custom tools',
                'Save changes',
                'Manage knowledge base',
                'Export agent',
                'Delete agent'
            ];
        }
        
        // Debug summary
        console.log('=== Context Summary ===');
        console.log('Agent:', data.agent.name, '| isNew:', data.agent.isNew);
        console.log('Total tools available:', data.tools.totalAvailable);
        console.log('Total tools selected:', data.tools.totalSelected);
        console.log('Selected tools:');
        data.tools.selectedTools.forEach(function(tool) {
            console.log('  -', tool.name, '(' + tool.toolId + '):', tool.description ? tool.description.substring(0, 60) + '...' : '(no description)');
        });
        console.log('Custom tools selected:', data.customTools.selected);
        
        return data;
    }
};

console.log('Custom Agent Enhanced context loaded - tools selector: #core-tool-categories input[name="core-tools"]');
