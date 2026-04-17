/**
 * Page Context: User Management
 * 
 * Provides real-time context about the user management page state.
 * Add this script to users.html
 * 
 * Extracts:
 * - List of users with roles
 * - Modal state (adding/editing)
 * - Current form values
 * - Available actions
 */

window.assistantPageContext = {
    page: 'users',
    pageName: 'User Management',
    
    getPageData: function() {
        const data = {
            // Current user access
            hasAccess: true,
            
            // User list
            users: {
                total: 0,
                byRole: {
                    admin: 0,
                    developer: 0,
                    endUser: 0
                },
                list: []
            },
            
            // Modal state
            modal: {
                isOpen: false,
                mode: 'add', // 'add' or 'edit'
                userId: null
            },
            
            // Form data (when modal is open)
            formData: {
                fullName: '',
                userName: '',
                email: '',
                phone: '',
                role: '',
                roleName: '',
                hasPassword: false
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
        
        // === USER TABLE ===
        const userTable = document.getElementById('userTable');
        if (userTable) {
            const rows = userTable.querySelectorAll('tbody tr');
            data.users.total = rows.length;
            
            rows.forEach(function(row) {
                const cells = row.querySelectorAll('td');
                if (cells.length >= 6) {
                    const roleCell = cells[5];
                    const badge = roleCell.querySelector('.badge');
                    const roleName = badge ? badge.textContent.trim() : 'Unknown';
                    
                    // Count by role
                    if (roleName === 'Admin') {
                        data.users.byRole.admin++;
                    } else if (roleName === 'Developer') {
                        data.users.byRole.developer++;
                    } else if (roleName === 'End User') {
                        data.users.byRole.endUser++;
                    }
                    
                    // Add to list (first 10 for context)
                    if (data.users.list.length < 10) {
                        data.users.list.push({
                            id: cells[0].textContent.trim(),
                            name: cells[1].textContent.trim(),
                            username: cells[2].textContent.trim(),
                            email: cells[3].textContent.trim(),
                            role: roleName
                        });
                    }
                }
            });
        }
        
        // === MODAL STATE ===
        const modal = document.getElementById('userModal');
        if (modal && modal.classList.contains('show')) {
            data.modal.isOpen = true;
            
            // Check if editing or adding
            const userIdField = document.getElementById('userId');
            if (userIdField && userIdField.value) {
                data.modal.mode = 'edit';
                data.modal.userId = userIdField.value;
            } else {
                data.modal.mode = 'add';
            }
            
            // Get form data
            const fullNameField = document.getElementById('fullName');
            const userNameField = document.getElementById('userName');
            const emailField = document.getElementById('email');
            const phoneField = document.getElementById('phone');
            const roleField = document.getElementById('permissions');
            const passwordField = document.getElementById('password');
            
            if (fullNameField) data.formData.fullName = fullNameField.value.trim();
            if (userNameField) data.formData.userName = userNameField.value.trim();
            if (emailField) data.formData.email = emailField.value.trim();
            if (phoneField) data.formData.phone = phoneField.value.trim();
            if (passwordField) data.formData.hasPassword = !!passwordField.value;
            
            if (roleField && roleField.value) {
                data.formData.role = roleField.value;
                const selectedOption = roleField.options[roleField.selectedIndex];
                data.formData.roleName = selectedOption ? selectedOption.text : '';
            }
            
            // Validation for modal
            if (!data.formData.fullName) {
                data.validation.errors.push('Full name is required');
            }
            if (!data.formData.userName) {
                data.validation.errors.push('Username is required');
            }
            if (!data.formData.email) {
                data.validation.errors.push('Email is required');
            }
            if (data.modal.mode === 'add' && !data.formData.hasPassword) {
                data.validation.errors.push('Password is required for new users');
            }
        }
        
        data.validation.isValid = data.validation.errors.length === 0;
        
        // === AVAILABLE ACTIONS ===
        if (data.modal.isOpen) {
            if (data.modal.mode === 'add') {
                data.availableActions = [
                    'Fill in user details',
                    'Set a password',
                    'Select appropriate role',
                    'Save to create user',
                    'Cancel to close'
                ];
            } else {
                data.availableActions = [
                    'Modify user details',
                    'Change role if needed',
                    'Leave password blank to keep current',
                    'Save changes',
                    'Cancel to discard'
                ];
            }
        } else {
            data.availableActions = [
                'Add a new user',
                'Edit an existing user',
                'Delete a user',
                'View user details'
            ];
        }
        
        // Debug summary
        console.log('=== Users Context ===');
        console.log('Total users:', data.users.total);
        console.log('By role - Admin:', data.users.byRole.admin, 
                   '| Developer:', data.users.byRole.developer,
                   '| End User:', data.users.byRole.endUser);
        console.log('Modal open:', data.modal.isOpen, '| Mode:', data.modal.mode);
        if (data.modal.isOpen) {
            console.log('Form - Name:', data.formData.fullName,
                       '| Username:', data.formData.userName,
                       '| Role:', data.formData.roleName);
        }
        
        return data;
    }
};

console.log('Users context loaded');
