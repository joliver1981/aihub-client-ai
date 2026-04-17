/**
 * Page Context: Group Security
 * 
 * Provides real-time context about the group security page state.
 * Add this script to groups.html
 * 
 * Extracts:
 * - Selected group and its details
 * - User assignments (assigned/unassigned)
 * - Agent permissions
 * - Form state and available actions
 */

window.assistantPageContext = {
    page: 'groups',
    pageName: 'Group Security',
    
    getPageData: function() {
        const data = {
            // Current user access
            hasAccess: true,
            
            // Selected group
            selectedGroup: {
                id: null,
                name: '',
                isSelected: false
            },
            
            // Group list
            groups: {
                total: 0,
                list: []
            },
            
            // User assignments
            users: {
                unassigned: {
                    total: 0,
                    selected: 0,
                    searchFilter: ''
                },
                assigned: {
                    total: 0,
                    selected: 0,
                    searchFilter: ''
                }
            },
            
            // Agent permissions
            permissions: {
                total: 0,
                granted: 0,
                searchFilter: '',
                agents: []
            },
            
            // Form state
            formState: {
                isAddingGroup: false,
                newGroupName: '',
                hasUnsavedChanges: false
            },
            
            // Available actions
            availableActions: [],
            
            // Validation
            validation: {
                isValid: true,
                errors: []
            }
        };
        
        // === CHECK ACCESS ===
        const accessDenied = document.querySelector('.alert-danger');
        if (accessDenied && accessDenied.textContent.includes('not authorized')) {
            data.hasAccess = false;
            data.availableActions = ['Contact administrator for access'];
            return data;
        }
        
        // === SELECTED GROUP ===
        const groupSelect = document.getElementById('groupSelect');
        if (groupSelect) {
            // Get all groups
            for (let i = 0; i < groupSelect.options.length; i++) {
                const option = groupSelect.options[i];
                if (option.value) {
                    data.groups.list.push({
                        id: option.value,
                        name: option.text.trim()
                    });
                }
            }
            data.groups.total = data.groups.list.length;
            
            // Get selected group
            if (groupSelect.value) {
                data.selectedGroup.id = groupSelect.value;
                data.selectedGroup.name = groupSelect.options[groupSelect.selectedIndex]?.text || '';
                data.selectedGroup.isSelected = true;
            }
        }
        
        // === CHECK IF ADDING NEW GROUP ===
        const newGroupNameInput = document.getElementById('newGroupName');
        const saveGroupBtn = document.getElementById('saveGroup');
        if (newGroupNameInput && newGroupNameInput.style.display !== 'none') {
            data.formState.isAddingGroup = true;
            data.formState.newGroupName = newGroupNameInput.value.trim();
        }
        
        // === UNASSIGNED USERS ===
        const unassignedList = document.getElementById('unassignedUsers');
        if (unassignedList) {
            const unassignedItems = unassignedList.querySelectorAll('.list-group-item');
            data.users.unassigned.total = unassignedItems.length;
            data.users.unassigned.selected = unassignedList.querySelectorAll('.list-group-item.active').length;
        }
        
        const unassignedSearch = document.getElementById('unassigned-search');
        if (unassignedSearch) {
            data.users.unassigned.searchFilter = unassignedSearch.value.trim();
        }
        
        // === ASSIGNED USERS ===
        const assignedList = document.getElementById('assignedUsers');
        if (assignedList) {
            const assignedItems = assignedList.querySelectorAll('.list-group-item');
            data.users.assigned.total = assignedItems.length;
            data.users.assigned.selected = assignedList.querySelectorAll('.list-group-item.active').length;
        }
        
        const assignedSearch = document.getElementById('assigned-search');
        if (assignedSearch) {
            data.users.assigned.searchFilter = assignedSearch.value.trim();
        }
        
        // === AGENT PERMISSIONS ===
        const permissionsList = document.getElementById('permissionsList');
        if (permissionsList) {
            const allCheckboxes = permissionsList.querySelectorAll('input[type="checkbox"]');
            const checkedBoxes = permissionsList.querySelectorAll('input[type="checkbox"]:checked');
            
            data.permissions.total = allCheckboxes.length;
            data.permissions.granted = checkedBoxes.length;
            
            // Get first few agent names for context
            const agentLabels = permissionsList.querySelectorAll('.checkbox-label');
            for (let i = 0; i < Math.min(5, agentLabels.length); i++) {
                const label = agentLabels[i];
                const checkbox = label.previousElementSibling;
                if (checkbox) {
                    data.permissions.agents.push({
                        name: label.textContent.trim().substring(0, 50),
                        granted: checkbox.checked
                    });
                }
            }
        }
        
        const permissionsSearch = document.getElementById('permissions-search');
        if (permissionsSearch) {
            data.permissions.searchFilter = permissionsSearch.value.trim();
        }
        
        // === AVAILABLE ACTIONS ===
        if (data.formState.isAddingGroup) {
            data.availableActions = [
                'Enter a name for the new group',
                'Click Save Group to create',
                'Click Cancel to abort'
            ];
        } else if (!data.selectedGroup.isSelected) {
            data.availableActions = [
                'Select a group from the dropdown',
                'Add a new group'
            ];
        } else {
            data.availableActions = [
                'Assign/unassign users to the group',
                'Grant/revoke agent permissions',
                'Save changes',
                'Delete the group',
                'Select a different group'
            ];
            
            if (data.users.unassigned.selected > 0) {
                data.availableActions.unshift(`Add ${data.users.unassigned.selected} selected user(s) to group`);
            }
            if (data.users.assigned.selected > 0) {
                data.availableActions.unshift(`Remove ${data.users.assigned.selected} selected user(s) from group`);
            }
        }
        
        // === VALIDATION ===
        if (data.formState.isAddingGroup && !data.formState.newGroupName) {
            data.validation.errors.push('Group name is required');
        }
        data.validation.isValid = data.validation.errors.length === 0;
        
        // Debug summary
        console.log('=== Groups Context ===');
        console.log('Selected group:', data.selectedGroup.name || '(none)');
        console.log('Total groups:', data.groups.total);
        console.log('Unassigned users:', data.users.unassigned.total, '| Selected:', data.users.unassigned.selected);
        console.log('Assigned users:', data.users.assigned.total, '| Selected:', data.users.assigned.selected);
        console.log('Permissions:', data.permissions.granted, '/', data.permissions.total, 'granted');
        console.log('Adding new group:', data.formState.isAddingGroup);
        
        return data;
    }
};

console.log('Groups context loaded');
