// static/js/doc_processor.js
// Document Processor specific functionality - adapted for Bootstrap 4

// Wrap everything in an IIFE to avoid global namespace pollution
(function() {
    // Initialize document processor specific functionality
    function initDocumentProcessor() {
        // Enable all tooltips in document processor components
        $('[data-toggle="tooltip"]').tooltip();

        // Enable all popovers in document processor components
        $('[data-toggle="popover"]').popover({
            html: true,
            trigger: 'focus'
        });

        // Confirmation dialog for dangerous actions in document processor
        $('.doc-processor .confirm-action').on('click', function(e) {
            if (!confirm($(this).data('confirm-message') || 'Are you sure you want to perform this action?')) {
                e.preventDefault();
                return false;
            }
            return true;
        });

        // Job form conditional fields
        function toggleBatchSize() {
            $('#BatchSize').prop('disabled', !$('#UseBatchProcessing').is(':checked'));
        }
        
        $('#UseBatchProcessing').on('change', toggleBatchSize);
        toggleBatchSize(); // Initialize on page load
        
        function toggleNotificationEmail() {
            $('#NotificationEmail').prop('disabled', !$('#NotifyOnCompletion').is(':checked'));
        }
        
        $('#NotifyOnCompletion').on('change', toggleNotificationEmail);
        toggleNotificationEmail(); // Initialize on page load

        // Directory browser functionality
        initDirectoryBrowser();
        
        // Auto-refresh for running job executions
        if ($('.job-status-refresh').length) {
            setInterval(function() {
                location.reload();
            }, 30000); // Refresh every 30 seconds
        }

        // Column sorting for tables
        $('.doc-processor .sortable').on('click', function() {
            var table = $(this).parents('table').eq(0);
            var rows = table.find('tr:gt(0)').toArray().sort(comparer($(this).index()));
            this.asc = !this.asc;
            if (!this.asc) {
                rows = rows.reverse();
            }
            for (var i = 0; i < rows.length; i++) {
                table.append(rows[i]);
            }
            
            // Update sort indicators
            table.find('th').removeClass('sorted-asc sorted-desc');
            $(this).addClass(this.asc ? 'sorted-asc' : 'sorted-desc');
        });

        function comparer(index) {
            return function(a, b) {
                var valA = getCellValue(a, index);
                var valB = getCellValue(b, index);
                return $.isNumeric(valA) && $.isNumeric(valB) ? valA - valB : valA.localeCompare(valB);
            };
        }

        function getCellValue(row, index) {
            return $(row).children('td').eq(index).text();
        }
    }
    
    // Initialize directory browser
    function initDirectoryBrowser() {
        let directoryModal = $('#directoryBrowserModal');
        if (directoryModal.length === 0) return;

        let currentTarget = null;
        let currentPath = '';

        // Helper: join path segments using backslash (Windows)
        function joinPath(basePath, child) {
            if (!basePath) return child; // root level — child is a drive like "C:\"
            // Remove trailing backslash from base, then join
            return basePath.replace(/\\$/, '') + '\\' + child;
        }

        // Helper: split a Windows path into breadcrumb parts
        // e.g. "C:\Users\docs" => ["C:\", "Users", "docs"]
        // e.g. "\\server\share\folder" => ["\\server\share", "folder"]
        function splitPath(path) {
            if (!path) return [];
            // Normalise to backslashes
            path = path.replace(/\//g, '\\');
            // UNC path: \\server\share[\rest...]
            let uncMatch = path.match(/^(\\\\[^\\]+\\[^\\]+)(\\.*)?$/);
            if (uncMatch) {
                let uncRoot = uncMatch[1]; // "\\server\share"
                let rest = (uncMatch[2] || '').replace(/^\\/, '').replace(/\\$/, '');
                let parts = rest ? rest.split('\\') : [];
                return [uncRoot].concat(parts);
            }
            // Match drive root like "C:\" then split remainder
            let match = path.match(/^([A-Za-z]:\\)(.*)/);
            if (match) {
                let drive = match[1]; // "C:\"
                let rest = match[2].replace(/\\$/, '');
                let parts = rest ? rest.split('\\') : [];
                return [drive].concat(parts);
            }
            // Fallback
            return path.split('\\').filter(Boolean);
        }

        // Build the cumulative path for breadcrumb index i
        function buildPath(parts, upToIndex) {
            if (upToIndex === 0) return parts[0]; // drive root e.g. "C:\"
            let p = parts[0];
            for (let i = 1; i <= upToIndex; i++) {
                p = p.replace(/\\$/, '') + '\\' + parts[i];
            }
            return p;
        }

        // Function to load directories
        function loadDirectories(path) {
            currentPath = path;
            $('#directoryPathInput').val(path);
            $('#directoryPicker').html('<div class="d-flex justify-content-center"><div class="spinner-border text-primary" role="status"><span class="sr-only">Loading...</span></div></div>');

            // Update breadcrumb
            let breadcrumb = '<li class="breadcrumb-item"><a href="#" data-path="">Computer</a></li>';
            if (path) {
                let parts = splitPath(path);
                for (let i = 0; i < parts.length; i++) {
                    let segPath = buildPath(parts, i);
                    if (i === parts.length - 1) {
                        breadcrumb += `<li class="breadcrumb-item active">${parts[i]}</li>`;
                    } else {
                        breadcrumb += `<li class="breadcrumb-item"><a href="#" data-path="${segPath}">${parts[i]}</a></li>`;
                    }
                }
            }
            $('#directoryBreadcrumb').html(breadcrumb);

            // Load directories from server
            $.ajax({
                url: '/directories',
                method: 'GET',
                data: { path: path || '/' },
                success: function(data) {
                    let html = '';
                    if (data.directories.length === 0) {
                        html = '<div class="text-center text-muted">No subdirectories found</div>';
                    } else {
                        for (let dir of data.directories) {
                            // If at root level, directories are drives like "C:\" — use as-is
                            let fullPath = data.is_root ? dir : joinPath(data.path, dir);
                            let displayName = data.is_root ? dir : dir;
                            let icon = data.is_root ? 'fa-hdd' : 'fa-folder';
                            html += `<div class="directory-item" data-path="${fullPath}">
                                        <i class="fas ${icon} mr-2"></i> ${displayName}
                                     </div>`;
                        }
                    }
                    $('#directoryPicker').html(html);

                    // Single click navigates into the directory
                    $('.directory-item').click(function() {
                        $('.directory-item').removeClass('selected');
                        $(this).addClass('selected');
                        loadDirectories($(this).data('path'));
                    });

                    // Double-click to select and close
                    $('.directory-item').dblclick(function() {
                        if (currentTarget) {
                            currentTarget.val($(this).data('path'));
                            directoryModal.modal('hide');
                        }
                    });
                },
                error: function(xhr, status, error) {
                    $('#directoryPicker').html(`<div class="alert alert-danger">Error loading directories: ${xhr.responseJSON?.error || error}</div>`);
                }
            });
        }

        // Browse button click handlers
        $('#browseInputBtn').click(function() {
            currentTarget = $('#InputDirectory');
            currentPath = currentTarget.val() || '';
            loadDirectories(currentPath);
            directoryModal.modal('show');
        });

        $('#browseArchiveBtn').click(function() {
            currentTarget = $('#ArchiveDirectory');
            currentPath = currentTarget.val() || '';
            loadDirectories(currentPath);
            directoryModal.modal('show');
        });

        // Path input Go button: navigate to typed path
        $('#directoryPathGoBtn').click(function() {
            let typedPath = $('#directoryPathInput').val().trim();
            if (typedPath) {
                loadDirectories(typedPath);
            }
        });

        // Enter key in path input triggers Go
        $('#directoryPathInput').on('keypress', function(e) {
            if (e.which === 13) {
                e.preventDefault();
                $('#directoryPathGoBtn').click();
            }
        });

        // Breadcrumb click handler
        $('#directoryBreadcrumb').on('click', 'a', function(e) {
            e.preventDefault();
            loadDirectories($(this).data('path'));
        });

        // Select directory button handler
        $('#selectDirectoryBtn').click(function() {
            if (currentTarget) {
                currentTarget.val(currentPath);
                directoryModal.modal('hide');
            }
        });
    }

    // Document processor utility functions (exposed globally)
    window.docProcessor = {
        // Function to show document processor notifications
        showNotification: function(message, type = 'info') {
            // Create toast container if it doesn't exist
            if ($('.doc-processor-toast-container').length === 0) {
                $('body').append('<div class="doc-processor-toast-container"></div>');
            }
            
            const toast = $(`
                <div class="toast bg-${type} text-white" role="alert" aria-live="assertive" aria-atomic="true">
                    <div class="toast-header bg-${type} text-white">
                        <strong class="mr-auto">Notification</strong>
                        <button type="button" class="ml-2 mb-1 close" data-dismiss="toast" aria-label="Close">
                            <span aria-hidden="true">&times;</span>
                        </button>
                    </div>
                    <div class="toast-body">
                        ${message}
                    </div>
                </div>
            `);
            
            $('.doc-processor-toast-container').append(toast);
            toast.toast({
                delay: 3000,
                autohide: true
            });
            toast.toast('show');
            
            // Remove toast when hidden
            toast.on('hidden.bs.toast', function() {
                $(this).remove();
            });
        },
        
        // Format dates for document processor UI
        formatDate: function(dateString) {
            if (!dateString) return '';
            return moment(dateString).format('YYYY-MM-DD HH:mm');
        },
        
        // Format duration for document processor UI
        formatDuration: function(seconds) {
            if (!seconds) return '';
            
            const hours = Math.floor(seconds / 3600);
            const minutes = Math.floor((seconds % 3600) / 60);
            const remainingSeconds = seconds % 60;
            
            let result = '';
            if (hours > 0) {
                result += hours + 'h ';
            }
            if (minutes > 0 || hours > 0) {
                result += minutes + 'm ';
            }
            result += remainingSeconds + 's';
            
            return result;
        },

        openDocument: function(filePath) {
            if (filePath) {
                // Use the new serve_document_2 route with query parameter
                const viewerUrl = `/document/serve?path=${encodeURIComponent(filePath)}`;
                window.open(viewerUrl, '_blank');
            } else {
                this.showToast('Document path not available', 'warning', 3000);
            }
        },
        
        // View document (placeholder function)
        viewDocument: function(documentId, filePath) {
            console.log('View document with ID:', documentId);
            console.log('View document with path:', filePath);
            //alert('View document with ID: ' + documentId + '\nDocument path:' + filePath + '\nFeature not implemented in this demo.');
            // Open the document
            this.openDocument(filePath);
        }
    };

    // Call init function when DOM is ready
    $(document).ready(initDocumentProcessor);
})();