let jsPlumbInstance;
let selectedNode = null;
let selectedConnection = null;
let nodeCounter = 0;
let startNode = null;

let nodeConfigs = new Map(); // Store node configurations
let configModal = null; // Will store the Bootstrap modal instance

let isWorkflowRunning = false;
let workflowExecution = null;
let configuredNode = null; // This will store the node being configured
// Holds the backend execution ID for cancel/pause operations during engine runs
let currentExecutionId = null;

let connData = null;
let agentData = null;

let workflowManagerModal = null;
let currentWorkflow = null;
let currentWorkflowName = null;

// Store categories globally for reference
let categoryMap = new Map(); // Will store id -> name mapping

let workflowVariables = {}; // Store workflow variables between nodes


// Debug mode state
let isDebugMode = false;
let debugLogEntries = [];
let nodeOutputs = {};
let executionPath = [];
let workflowVariableDefinitions = {};
let debugPanelCollapsed = false;


// Category Management Functions
let categoryManagerModal = null;
let currentlyEditingCategoryId = 0;

let isEditingVariable = false;
let originalVariableName = null;


document.addEventListener('DOMContentLoaded', function() {

    // Initialize jsPlumb with default settings
    jsPlumbInstance = jsPlumb.getInstance({
        ConnectionsDetachable: true,
        Container: "workflow-canvas",
        Connector: ["Bezier", { curviness: 50 }],
        DragOptions: { 
            cursor: "pointer", 
            zIndex: 2000,
            // Prevent jsPlumb from using transforms
            constrainFunction: function(pos) {
                return pos;
            }
        },
        PaintStyle: { strokeWidth: 2, stroke: "#28a745" },
        EndpointStyle: { 
            fill: "#456", 
            stroke: "#2a2929",
            strokeWidth: 1,
            radius: 7,
            outlineStroke: "transparent",
            outlineWidth: 4
        },
        HoverPaintStyle: { stroke: "#c63", strokeWidth: 3 },
        EndpointHoverStyle: { fill: "#c63" },
        ConnectionOverlays: [
            ["Arrow", { 
                width: 12, 
                length: 12, 
                location: 0.90,  // Move back from endpoint (was 1)
                foldback: 0.8,
                id: "arrow"
            }]
        ]
    });

    // Load certain tools this way...
    // const toolbar = document.querySelector('.toolbar');
    //         if (toolbar) {
    //             const setVarItem = document.createElement('div');
    //             setVarItem.className = 'tool-item';
    //             setVarItem.setAttribute('draggable', 'true');
    //             setVarItem.setAttribute('data-type', 'Set Variable');
    //             setVarItem.innerHTML = '<i class="bi bi-braces"></i> Set Variable';

    //             const folderSelectorItem = document.createElement('div');
    //             folderSelectorItem.className = 'tool-item';
    //             folderSelectorItem.setAttribute('draggable', 'true');
    //             folderSelectorItem.setAttribute('data-type', 'Folder Selector');
    //             folderSelectorItem.innerHTML = '<i class="bi bi-folder2"></i> Folder Selector';

    //             const humanItem = document.createElement('div');
    //             humanItem.className = 'tool-item';
    //             humanItem.setAttribute('draggable', 'true');
    //             humanItem.setAttribute('data-type', 'Human Approval');
    //             humanItem.innerHTML = '<i class="bi bi-person-check"></i> Human Approval';
                
    //             const aiActionItem = toolbar.querySelector('.ai-action');
    //             if (aiActionItem) {
    //                 toolbar.insertBefore(humanItem, aiActionItem); 
    //                 toolbar.insertBefore(folderSelectorItem, aiActionItem); 
    //                 toolbar.insertBefore(setVarItem, aiActionItem);
    //             } else {
    //                 toolbar.appendChild(setVarItem);
    //                 toolbar.appendChild(folderSelectorItem);
    //                 toolbar.appendChild(humanItem);
    //             }

    //             const conditionalItem = document.createElement('div');
    //             conditionalItem.className = 'tool-item';
    //             conditionalItem.setAttribute('draggable', 'true');
    //             conditionalItem.setAttribute('data-type', 'Conditional');
    //             conditionalItem.innerHTML = '<i class="bi bi-diagram-3"></i> Conditional';

    //             const setVarItem2 = toolbar.querySelector('[data-type="Set Variable"]');
    //             if (setVarItem2) {
    //                 setVarItem2.parentNode.insertBefore(conditionalItem, setVarItem2.nextSibling);
    //             } else {
    //                 toolbar.appendChild(conditionalItem);
    //             }

    //             // Add drag event listeners
    //             conditionalItem.addEventListener('dragstart', (e) => {
    //                 e.dataTransfer.setData('nodeType', 'Conditional');
    //             });

    //             const loopItem = document.createElement('div');
    //             loopItem.className = 'tool-item';
    //             loopItem.setAttribute('draggable', 'true');
    //             loopItem.setAttribute('data-type', 'Loop');
    //             loopItem.innerHTML = '<i class="bi bi-arrow-repeat"></i> Start Loop';

    //             const conditionalItem2 = toolbar.querySelector('[data-type="Conditional"]');
    //             if (conditionalItem2) {
    //                 conditionalItem2.parentNode.insertBefore(loopItem, conditionalItem2.nextSibling);
    //             } else {
    //                 toolbar.appendChild(loopItem);
    //             }

    //             // Add drag event listeners
    //             loopItem.addEventListener('dragstart', (e) => {
    //                 e.dataTransfer.setData('nodeType', 'Loop');
    //             });

    //                 const emptyBehaviorSelect = document.querySelector('select[name="emptyBehavior"]');
    //                 if (emptyBehaviorSelect) {
    //                     emptyBehaviorSelect.addEventListener('change', function() {
    //                         const defaultGroup = document.getElementById('default-value-group');
    //                         if (defaultGroup) {
    //                             defaultGroup.style.display = this.value === 'default' ? 'block' : 'none';
    //                         }
    //                     });
    //                 }

    //             const endLoopItem = document.createElement('div');
    //             endLoopItem.className = 'tool-item';
    //             endLoopItem.setAttribute('draggable', 'true');
    //             endLoopItem.setAttribute('data-type', 'End Loop');
    //             endLoopItem.innerHTML = '<i class="bi bi-arrow-return-left"></i> End Loop';

    //             const loopItem2 = toolbar.querySelector('[data-type="Loop"]');
    //             if (loopItem2) {
    //                 loopItem2.parentNode.insertBefore(endLoopItem, loopItem2.nextSibling);
    //             } else {
    //                 toolbar.appendChild(endLoopItem);
    //             }

    //             // Add drag event listeners
    //             endLoopItem.addEventListener('dragstart', (e) => {
    //                 e.dataTransfer.setData('nodeType', 'End Loop');
    //             });
    //         }

    // Setup drag and drop from toolbar
    setupToolbarDragAndDrop();
    
    // Setup context menus
    setupContextMenus();

    // Load existing database connections
    loadConnections();

    // Load existing agents
    loadAgents();

    // Prevent text selection during drag
    document.addEventListener('selectstart', function(e) {
        let targetElement = e.target;
        while (targetElement != null) {
            if (targetElement.classList && targetElement.classList.contains('workflow-node')) {
                e.preventDefault();
                break;
            }
            targetElement = targetElement.parentElement;
        }
    });

        // Initialize the workflow manager modal
        workflowManagerModal = new bootstrap.Modal(document.getElementById('workflowManagerModal'));
    
        // Set up search and filter handlers
        document.getElementById('workflowSearch').addEventListener('input', filterWorkflows);
        document.getElementById('categoryFilter').addEventListener('change', filterWorkflows);
        
        // Load initial data
        loadCategories();
        populateWorkflowsDropdown();


            // Initialize debug panel
            initializeDebugPanel();
            
            // Initialize workflow variables modal
            const workflowVariablesModal = new bootstrap.Modal(document.getElementById('workflowVariablesModal'));
            
            // Load workflow variable definitions from localStorage if available
            //loadWorkflowVariableDefinitions();


             // Initialize category manager modal
            categoryManagerModal = new bootstrap.Modal(document.getElementById('categoryManagerModal'));
            
            // Set up category management event handlers
            document.getElementById('addCategoryBtn').addEventListener('click', showAddCategoryForm);
            document.getElementById('cancelCategoryBtn').addEventListener('click', hideCategoryForm);
            document.getElementById('saveCategoryBtn').addEventListener('click', saveCategory);
  

    // Setup debug panel resizing
    setupDebugPanelResizing();

    configModal = new bootstrap.Modal(document.getElementById('nodeConfigModal'));

    // Enhance config templates with variable selectors
    enhanceConfigTemplates();

    // Initialize this in your document ready function
    enhanceLoadWorkflow();

    // Initialize the current workflow display
    updateCurrentWorkflowDisplay();

    enableDebugMode(true); // Enable debug mode by default (this should never be disabled)
});

function setupToolbarDragAndDrop() {
    const toolItems = document.querySelectorAll('.tool-item');
    const canvas = document.getElementById('workflow-canvas');

    console.log('Setting up toolbar drag and drop');

    // Empty behavior Select Event Listener
    document.addEventListener('change', function(e) {
        if (e.target && e.target.name === 'emptyBehavior') {
            const defaultGroup = document.getElementById('default-value-group');
            if (defaultGroup) {
                defaultGroup.style.display = e.target.value === 'default' ? 'block' : 'none';
            }
        }
    });

    // Legacy drag and drop logic
    // toolItems.forEach(item => {
    //     item.addEventListener('dragstart', (e) => {
    //         e.dataTransfer.setData('text/plain', item.dataset.type);
    //         console.log('Drag started:', item.dataset.type);
    //     });
    // });

    toolItems.forEach(item => {
        item.addEventListener('dragstart', (e) => {
            e.dataTransfer.setData('text/plain', item.dataset.type);
            // Add visual feedback
            item.classList.add('dragging');
            // Set drag effect
            e.dataTransfer.effectAllowed = 'copy';
            console.log('Drag started:', item.dataset.type);
        });
        
        item.addEventListener('dragend', (e) => {
            item.classList.remove('dragging');
        });
    });

    canvas.addEventListener('dragover', (e) => {
        e.preventDefault();
    });

    canvas.addEventListener('drop', (e) => {
        e.preventDefault();
        const type = e.dataTransfer.getData('text/plain');
        
        // Get the canvas's position and dimensions
        const canvasRect = canvas.getBoundingClientRect();
        
        // Account for both canvas position and scroll position
        const canvasScrollTop = canvas.scrollTop || 0;
        const documentScrollTop = document.documentElement.scrollTop || document.body.scrollTop || 0;
        
        // Calculate position relative to canvas, including scroll offsets
        const x = e.clientX - canvasRect.left;
        // For y-position, explicitly account for both canvas and document scroll
        const y = e.clientY - canvasRect.top + canvasScrollTop;
        
        console.log('Drop position calculation:', {
            clientY: e.clientY,
            canvasTop: canvasRect.top,
            canvasScrollTop: canvasScrollTop,
            documentScrollTop: documentScrollTop,
            calculatedY: y
        });
        
        // Create the node at the calculated position
        createNode(type, x, y);
        
        // Log the node position after creation
        setTimeout(() => {
            const nodes = document.querySelectorAll('.workflow-node');
            const lastNode = nodes[nodes.length - 1];
            if (lastNode) {
                console.log('Node created at:', {
                    top: lastNode.style.top,
                    left: lastNode.style.left
                });
            }
        }, 10);
    });
}

// Agent Configuration Functions
function loadAgents() {
    fetch('/get/agents')
        .then(response => {
            console.log('Response received:', response);
            if (!response.ok) {
                throw new Error('Network response was not ok ' + response.statusText);
            }
            return response.json();
        })
        .then(data => {
            console.log('Fetched agents:', data.data);
            //data = JSON.parse(data);
            agentData = data.data;
        })
        .catch(error => {
            console.error('Error fetching agents:', error);
        });
}

function populateExistingAgents(data) {
    const connSelect = document.getElementById('agent-dropdown');
    connSelect.innerHTML = '';

    data.forEach(conn => { 
        const option = document.createElement('option');
        option.value = conn.agent_id;
        option.textContent = conn.agent_description;
        connSelect.appendChild(option);
    });

    if (data.length > 999) {
        connSelect.selectedIndex = 0;
        //updateFormFields(data[0]);
    }

    console.log('Form populated with agent data');
}

// Database Configuration Functions
function loadConnections() {
    fetch('/get/connections')
        .then(response => {
            console.log('Response received:', response);
            if (!response.ok) {
                throw new Error('Network response was not ok ' + response.statusText);
            }
            return response.json();
        })
        .then(data => {
            console.log('Fetched connections:', data);
            data = JSON.parse(data);
            connData = data;
            //populateExistingConnections(data);
        })
        .catch(error => {
            console.error('Error fetching connections:', error);
        });
}

async function getDocumentPort() {
    try {
      const response = await fetch('/document/config');
      console.log('Response received:', response);
      
      if (!response.ok) {
        throw new Error('Network response was not ok ' + response.statusText);
      }
      
      const data = await response.json();
      console.log('Fetched config (data):', data);
      console.log(`Port: ${data.port}`);
      return data.port;
    } catch (error) {
      console.error('Error fetching document port:', error);
      return 3011; // Default fallback port
    }
  }

async function getDocumentURL(documentRoute) {
    const API_PORT = await getDocumentPort(); // Fallback to 3011 if not set

    // Create a URL object based on the current window.location
    const url = new URL(documentRoute, window.location.origin);

    // Replace just the port
    url.port = API_PORT;

    return url.toString();
}

async function executeDatabaseQuery(connection_id, query) {
    // Encode the query parameter to handle special characters in URLs
    const encodedQuery = encodeURIComponent(query);
    
    // Construct the URL with properly encoded parameters
    const url = `/execute/query_result/${connection_id}/${encodedQuery}`;
    
    return fetch(url, {
        method: 'GET',
        headers: {
            'Accept': 'application/json'
        }
    })
    .then(response => {
        console.log('Response received:', response);
        if (!response.ok) {
            throw new Error(`Network response was not ok: ${response.status} ${response.statusText}`);
        }
        return response.json();
    })
    .then(data => {
        console.log('Successfully executed query:', data);
        if (data.status == 'success')
            return data; // Return the data for further processing if needed
        else
            return data;
    })
    .catch(error => {
        console.error('Error executing query:', error);
        throw error; // Re-throw the error for handling by the caller
    });
}


// async function sendEmailNotification(email_to, subject, message) {
//     // Encode the query parameter to handle special characters in URLs
//     const encodedTo = encodeURIComponent(email_to);
//     const encodedSubj = encodeURIComponent(subject);
//     const encodedMsg = encodeURIComponent(message);
//     console.log(`Sending email notification to ${email_to}`);
//     // Construct the URL with properly encoded parameters
//     const url = `/notification/email/${encodedTo}/${encodedSubj}/${encodedMsg}`;
    
//     return fetch(url, {
//         method: 'GET',
//         headers: {
//             'Accept': 'application/json'
//         }
//     })
//     .then(response => {
//         console.log('Response received:', response);
//         if (!response.ok) {
//             throw new Error(`Network response was not ok: ${response.status} ${response.statusText}`);
//         }
//         return response.json();
//     })
//     .then(data => {
//         console.log('Successfully sent notification:', data);
//         if (data.status == 'success')
//             return true; // Return the data for further processing if needed
//         else
//             return false;
//     })
//     .catch(error => {
//         console.error('Error sending notification:', error);
//         throw error; // Re-throw the error for handling by the caller
//     });
// }

async function sendEmailNotification(email_to, subject, message = '') {
    const encodedTo = encodeURIComponent(email_to);
    const encodedSubj = encodeURIComponent(subject);
    
    console.log(`Sending email notification to ${email_to}`);
    
    // Always use POST with JSON for consistency and reliability
    const url = `/notification/email/${encodedTo}/${encodedSubj}`;
    
    return fetch(url, {
        method: 'POST',
        headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ message: message || '' })
    })
    .then(response => {
        console.log('Response received:', response);
        if (!response.ok) {
            throw new Error(`Network response was not ok: ${response.status} ${response.statusText}`);
        }
        return response.json();
    })
    .then(data => {
        console.log('Successfully sent notification:', data);
        return data.status === 'success';
    })
    .catch(error => {
        console.error('Error sending notification:', error);
        throw error;
    });
}


function populateExistingConnections(data) {
    const connSelect = document.getElementById('existing-connections');
    connSelect.innerHTML = '';

    data.forEach(conn => { 
        const option = document.createElement('option');
        option.value = conn.id;
        option.textContent = conn.connection_name;
        connSelect.appendChild(option);
    });

    if (data.length > 999) {
        connSelect.selectedIndex = 0;
        //updateFormFields(data[0]);
    }

    console.log('Form populated with connection data');
}



// Function to toggle document fields based on selected action
function toggleDocumentFields() {
    const action = document.getElementById('document-action-select')?.value || 'process';
    
    // Hide all action-specific fields first
    document.querySelectorAll('.document-field').forEach(field => {
        if (field.id !== 'source-document-field' && field.id !== 'output-field') {
            field.style.display = 'none';
        }
    });
    
    // Always show these fields
    if (document.getElementById('source-document-field')) {
        document.getElementById('source-document-field').style.display = 'block';
    }
    if (document.getElementById('output-field')) {
        document.getElementById('output-field').style.display = 'block';
    }
    
    // Show fields relevant to selected action
    switch(action) {
        case 'process':
            if (document.getElementById('document-type-field')) {
                document.getElementById('document-type-field').style.display = 'block';
            }
            if (document.getElementById('page-range-field')) {
                document.getElementById('page-range-field').style.display = 'block';
            }
            break;
        case 'extract':
            if (document.getElementById('document-type-field')) {
                document.getElementById('document-type-field').style.display = 'block';
            }
            if (document.getElementById('extraction-field')) {
                document.getElementById('extraction-field').style.display = 'block';
            }
            if (document.getElementById('page-range-field')) {
                document.getElementById('page-range-field').style.display = 'block';
            }
            break;
        case 'analyze':
            if (document.getElementById('prompt-field')) {
                document.getElementById('prompt-field').style.display = 'block';
            }
            break;
        case 'get':
            if (document.getElementById('document-id-field')) {
                document.getElementById('document-id-field').style.display = 'none';
            }
            if (document.getElementById('source-document-field')) {
                document.getElementById('source-document-field').style.display = 'block';
            }
            break;
        case 'save':
            // Source and output fields are already visible
            break;
    }

    // Hide or show document sharing and advanced options based on action
    const documentSharingFields = document.querySelectorAll('.process-field, .extract-field');
    const advancedOptionsButton = document.getElementById('advanced-document-options-button');
    const advancedOptionsField = document.getElementById('advanced-document-options');

    if (action === 'save') {
        // Hide document sharing and advanced options for Create Document action
        documentSharingFields.forEach(field => field.style.display = 'none');
        if (advancedOptionsField) advancedOptionsField.style.display = 'none';
        if (advancedOptionsButton) advancedOptionsButton.style.display = 'none';
    } else if (action === 'analyze' || action === 'get') {
        // Also hide for analyze and get actions
        documentSharingFields.forEach(field => field.style.display = 'none');
        if (advancedOptionsField && (action === 'analyze' || action === 'get')) {
            advancedOptionsField.style.display = 'none';
        }
        if (advancedOptionsButton && (action === 'analyze' || action === 'get')) {
            advancedOptionsButton.style.display = 'none';
        }
    } else {
        // Show them for process and extract actions
        documentSharingFields.forEach(field => field.style.display = 'block');
        if (advancedOptionsField) advancedOptionsField.style.display = 'block';
        if (advancedOptionsButton) advancedOptionsButton.style.display = 'block';
    }

}

// Function to toggle advanced options
function toggleAdvancedOptions() {
    const advancedOptions = document.getElementById('advanced-document-options');
    if (advancedOptions) {
        advancedOptions.classList.toggle('d-none');
    }
}

// Function to load document types
async function loadDocumentTypes() {
    try {
        const docTypesRoute = await getDocumentURL('/document/types');
        const response = await fetch(docTypesRoute);
        if (!response.ok) {
            throw new Error('Failed to load document types');
        }
        
        const data = await response.json();

        console.log(`Got document types: ${formatJsonOutput(data)}`);
        
        if (data.status === 'success' && data.document_types) {
            // Populate document type dropdown
            const select = document.getElementById('document-type-select');
            if (select) {
                // Keep the auto-detect option
                select.innerHTML = '<option value="auto">Auto-detect</option>';
                
                // Parse the document_types if it's a string
                let documentTypes = [];
                try {
                    if (typeof data.document_types === 'string') {
                        // Parse the JSON string
                        const parsedData = JSON.parse(data.document_types);
                        
                        // Check if it contains a nested document_types array
                        if (parsedData && Array.isArray(parsedData.document_types)) {
                            documentTypes = parsedData.document_types;
                        }
                    } else if (Array.isArray(data.document_types)) {
                        // If it's already an array, use it directly
                        documentTypes = data.document_types;
                    } else if (typeof data.document_types === 'object') {
                        // If it's an object, handle it as before
                        Object.entries(data.document_types).forEach(([id, name]) => {
                            const option = document.createElement('option');
                            option.value = id;
                            option.textContent = name;
                            select.appendChild(option);
                        });
                        // Return early since we've already added the options
                        return;
                    }
                    
                    // Add each document type
                    documentTypes.forEach(type => {
                        const option = document.createElement('option');
                        option.value = type;
                        option.textContent = type;
                        select.appendChild(option);
                    });
                    
                    console.log(`Added ${documentTypes.length} document types to dropdown`);
                } catch (error) {
                    console.error('Error parsing document types:', error);
                    console.log('Raw document_types data:', data.document_types);
                }
            }
        }
    } catch (error) {
        console.error('Error loading document types:', error);
    }
}



// Configuration templates for each node type
const nodeConfigTemplates = {
    'Database': {
        template: `
        <div class="mb-3">
            <label class="form-label">Database Operation</label>
            <select class="form-control" name="dbOperation" id="db-operation-select" onchange="toggleDbOperationFields()">
                <option value="query">Execute Query</option>
                <option value="procedure">Execute Stored Procedure</option>
                <option value="select">Select Data</option>
                <option value="insert">Insert Data</option>
                <option value="update">Update Data</option>
                <option value="delete">Delete Data</option>
            </select>
        </div>
        
        <div class="mb-3">
            <label for="existing-connections">Database Connection:</label>
            <select class="form-control" name="connection" id="existing-connections" required>
                <!-- Connections will be populated here -->
            </select>
        </div>
        
        <div id="db-query-group" class="mb-3">
        <label class="form-label">SQL Query</label>    
            <div class="input-group">
                <textarea class="form-control" name="query" rows="5" placeholder="SELECT * FROM table"></textarea>
                <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                    <i class="bi bi-braces"></i>
                </button>
            </div>
            <small class="form-text text-muted">
                You can use variables in your query with $\{varName\} syntax.
            </small>
        </div>
        
        <div id="db-procedure-group" class="mb-3" style="display: none;">
            <label class="form-label">Stored Procedure</label>
            <div class="input-group">
                <input type="text" class="form-control" name="procedure" placeholder="ProcedureName">

            </div>
            
            <div class="mt-3">
                <label class="form-label">Parameters (JSON format)</label>
                <div class="input-group">
                    <textarea class="form-control" name="parameters" rows="3" placeholder='[{"name": "param1", "value": "value1", "type": "string"}, {"name": "param2", "value": 123, "type": "number"}]'></textarea>
                    <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                        <i class="bi bi-braces"></i>
                    </button>
                </div>
                <small class="form-text text-muted">
                    Parameters as JSON array. You can use $\{varName\} syntax for parameter values.
                </small>
            </div>
        </div>
        
        <div id="db-table-group" class="mb-3" style="display: none;">
            <label class="form-label">Table Name</label>
            <div class="input-group">
                <input type="text" class="form-control" name="tableName" placeholder="TableName">
                <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                    <i class="bi bi-braces"></i>
                </button>
            </div>
            
            <div id="db-columns-group" class="mt-3">
                <label class="form-label">Columns (for SELECT)</label>
                <div class="input-group">
                    <input type="text" class="form-control" name="columns" placeholder="column1, column2, column3">
                    <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                        <i class="bi bi-braces"></i>
                    </button>
                </div>
                <small class="form-text text-muted">
                    Comma-separated list of columns, or * for all columns
                </small>
            </div>
            
            <div id="db-where-group" class="mt-3">
                <label class="form-label">WHERE Clause</label>
                <div class="input-group">
                    <input type="text" class="form-control" name="whereClause" placeholder="column = 'value'">
                    <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                        <i class="bi bi-braces"></i>
                    </button>
                </div>
                <small class="form-text text-muted">
                    WITHOUT the 'WHERE' keyword. You can use $\{varName\} syntax.
                </small>
            </div>
            
            <div id="db-data-group" class="mt-3" style="display: none;">
                <label class="form-label">Data (for INSERT/UPDATE)</label>
                <div class="input-group">
                    <select class="form-control" name="dataSource" id="db-data-source" onchange="toggleDbDataSource()">
                        <option value="direct">JSON Object</option>
                        <option value="variable">From Variable</option>
                        <option value="previous">From Previous Step</option>
                    </select>
                </div>
                
                <div id="db-direct-data" class="mt-2">
                    <div class="input-group">
                        <textarea class="form-control" name="data" rows="3" placeholder='{"column1": "value1", "column2": 123}'></textarea>
                        <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                            <i class="bi bi-braces"></i>
                        </button>
                    </div>
                </div>
                
                <div id="db-variable-data" class="mt-2" style="display: none;">
                    <div class="input-group">
                        <input type="text" class="form-control" name="dataVariable" placeholder="Variable name">
                        <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                            <i class="bi bi-braces"></i>
                        </button>
                    </div>
                </div>
                
                <div id="db-previous-data" class="mt-2" style="display: none;">
                    <div class="input-group">
                        <input type="text" class="form-control" name="dataPath" placeholder="Path in previous output (e.g., data.results)">
                        <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                            <i class="bi bi-braces"></i>
                        </button>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="mb-3">
            <label class="form-label">Output Settings</label>
            <div class="form-check mb-2">
                <input class="form-check-input" type="checkbox" name="saveToVariable" id="save-db-output" checked>
                <label class="form-check-label" for="save-db-output">
                    Save output to variable
                </label>
            </div>
            
            <div id="db-variable-output" class="input-group">
                <input type="text" class="form-control" name="outputVariable" placeholder="Variable name to store result" value="dbResult">
                <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                    <i class="bi bi-braces"></i>
                </button>
            </div>
            <small class="form-text text-muted">For queries, this will contain the result set. For other operations, it will contain success status and affected rows.</small>
        </div>
        
        <div class="mb-3">
            <label class="form-label">Error Handling</label>
            <div class="form-check">
                <input class="form-check-input" type="checkbox" name="continueOnError" id="db-continue-on-error">
                <label class="form-check-label" for="db-continue-on-error">
                    Continue workflow if operation fails
                </label>
            </div>
        </div>`,
    defaultConfig: {
        dbOperation: 'query',
        connection: '',
        query: '',
        procedure: '',
        parameters: '',
        tableName: '',
        columns: '*',
        whereClause: '',
        dataSource: 'direct',
        data: '{}',
        dataVariable: '',
        dataPath: '',
        saveToVariable: true,
        outputVariable: 'dbResult',
        continueOnError: false
    }
    },
    'File': {
        template: `
        <div class="mb-3">
            <label class="form-label">File Operation</label>
            <select class="form-control" name="operation" id="file-operation-select" onchange="toggleFileOperationFields()">
                <option value="read">Read File</option>
                <option value="write">Write to File</option>
                <option value="append">Append to File</option>
                <option value="check">Check if File Exists</option>
                <option value="delete">Delete File</option>
                <option value="copy">Copy File</option>
                <option value="move">Move File</option>
            </select>
        </div>
        
        <div class="mb-3">
            <label class="form-label">File Path</label>
            <div class="input-group">
                <input type="text" class="form-control" name="filePath" placeholder="/path/to/file">

            </div>
            <small class="form-text text-muted">Full path to the file. You can use $\{varName\} for variables.</small>
        </div>

        <div id="file-destination-section" class="mb-3" style="display: none;">
            <label class="form-label">Destination Path</label>
            <div class="input-group">
                <input type="text" class="form-control" name="destinationPath" placeholder="/path/to/destination">
                <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                    <i class="bi bi-braces"></i>
                </button>
            </div>
            <small class="form-text text-muted">Destination path for copy/move operations. You can use $\{varName\} for variables.</small>
        </div>

        <!-- Content Source Section for write/append -->
        <div id="file-content-section" class="mb-3" style="display: none;">
        <div id="file-content-section-inner" class="mb-3">
            <label class="form-label">Content Source</label>
            <select class="form-control" name="contentSource" id="file-content-source" onchange="toggleFileContentSource()">
                <option value="direct">Direct Input</option>
                <option value="variable">From Variable</option>
                <option value="previous">From Previous Step</option>
            </select>
            
            <!-- Direct Input Option -->
            <div id="file-direct-content" class="mt-2">
                <label class="form-label">Content to Write</label>
                <div class="input-group">
                    <textarea class="form-control" name="content" rows="5" placeholder="Content to write to file"></textarea>
                    <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                        <i class="bi bi-braces"></i>
                    </button>
                </div>
                <small class="form-text text-muted">You can use $\{varName\} syntax to include variables.</small>
            </div>
            
            <!-- From Variable Option -->
            <div id="file-variable-content" class="mt-2">
                <label class="form-label">Select Variable with Content</label>
                <div class="input-group">
                    <input type="text" class="form-control" name="contentVariable" placeholder="Variable name containing the content">
                    <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                        <i class="bi bi-braces"></i>
                    </button>
                </div>
                <small class="form-text text-muted">Enter the name of the variable that contains the content to write.</small>
            </div>
            
            <!-- From Previous Step Option -->
            <div id="file-previous-content" class="mt-2">
                <label class="form-label">Path in Previous Output</label>
                <div class="input-group">
                    <input type="text" class="form-control" name="contentPath" placeholder="Path in previous output (e.g., data.text)">
                    <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                        <i class="bi bi-braces"></i>
                    </button>
                </div>
                <small class="form-text text-muted">Specify the path to the content in the previous node's output.</small>
            </div>
        </div>
        </div>
        
        <!-- Output Settings Section -->
        <div id="file-output-section" class="mb-3">
            <div class="form-check mb-2">
                <input class="form-check-input" type="checkbox" name="saveToVariable" id="save-file-output" checked>
                <label class="form-check-label" for="save-file-output">
                    Store result in a variable
                </label>
            </div>
            
            <div id="file-output-variable-group">
                <label class="form-label">Output Variable Name</label>
                <div class="input-group">
                    <input type="text" class="form-control" name="outputVariable" value="fileOutput" placeholder="Variable name to store result">
                    <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                        <i class="bi bi-braces"></i>
                    </button>
                </div>
                <small id="file-output-help" class="form-text text-muted">
                    For read operations, this variable will contain the file content.
                    For other operations, it will contain the operation status.
                </small>
            </div>
        </div>
        
        <div class="mb-3">
            <label class="form-label">Error Handling</label>
            <div class="form-check">
                <input class="form-check-input" type="checkbox" name="continueOnError" id="continue-on-error">
                <label class="form-check-label" for="continue-on-error">
                    Continue workflow if operation fails
                </label>
            </div>
        </div>`,
    defaultConfig: {
        operation: 'read',
        filePath: '',
        destinationPath: '',
        contentSource: 'direct',
        content: '',
        contentVariable: '',
        contentPath: '',
        saveToVariable: true,
        outputVariable: '',
        continueOnError: false
    }
    },
    'Server': {
        template: `
            <div class="mb-3">
                <label class="form-label">URL</label>
                <input type="text" class="form-control" name="url" placeholder="https://api.example.com">
            </div>
            <div class="mb-3">
                <label class="form-label">Method</label>
                <select class="form-control" name="method">
                    <option value="GET">GET</option>
                    <option value="POST">POST</option>
                    <option value="PUT">PUT</option>
                    <option value="DELETE">DELETE</option>
                </select>
            </div>
            <div class="mb-3">
                <label class="form-label">Headers</label>
                <textarea class="form-control" name="headers" rows="3" placeholder='{"Content-Type": "application/json"}'></textarea>
            </div>`,
        defaultConfig: {
            url: '',
            method: 'GET',
            headers: '{}'
        }
    },
    'Alert': {
        template: `
            <div class="mb-3">
                <label class="form-label">Alert Type</label>
                <select class="form-control" name="alertType" onchange="updateAlertFields(this.value)">
                    <option value="email">Email</option>
                    <option value="text">Text</option>
                    <option value="call">Call</option>
                </select>
            </div>
            <div class="mb-3">
                <label class="form-label">Recipients</label>
                <input type="text" class="form-control" name="recipients" placeholder="user@example.com">
            </div>
            <div class="mb-3">
                <label class="form-label">Message Template</label>
                <div class="input-group">
                    <textarea class="form-control" name="messageTemplate" rows="3" placeholder=""></textarea>
                    <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                        <i class="bi bi-braces"></i>
                    </button>
                </div>
            </div>
            <div id="email-specific-fields">
                <div class="mb-3">
                    <label class="form-label">Email Subject</label>
                    <div class="input-group">
                        <input type="text" class="form-control" name="emailSubject" placeholder="Workflow Notification">
                    </div>
                    <small class="form-text text-muted">Leave blank to use default subject. Supports \${varName} variables.</small>
                </div>
                <div class="mb-3">
                    <label class="form-label">Attachment Path</label>
                    <div class="input-group">
                        <input type="text" class="form-control" name="attachmentPath" placeholder="/path/to/file or \${variable}">
                    </div>
                    <small class="form-text text-muted">Optional file path to attach. Use \${varName} for a variable from a previous step.</small>
                </div>
            </div>
            `,
        defaultConfig: {
            alertType: 'email',
            recipients: '',
            messageTemplate: '',
            emailSubject: '',
            attachmentPath: ''
        }
    },
// Update the Document template in nodeConfigTemplates
'Document': {
    template: `
        <div class="mb-3">
            <label class="form-label">Document Action</label>
            <select class="form-control" name="documentAction" id="document-action-select" onchange="toggleDocumentFields()">
                <option value="process">Process Document</option>
                <option value="extract">Extract Fields</option>
                <option value="analyze">Analyze with AI</option>
                <option value="save">Create Document</option>
            </select>
        </div>
        
        <!-- Document Source -->
        <div class="mb-3 document-field" id="source-document-field">
            <label class="form-label">Document Source</label>
            <div class="input-group">
                <select class="form-control" name="sourceType">
                    <option value="file">File Path</option>
                    <option value="variable">Workflow Variable</option>
                    <option value="previous">Previous Step Output</option>
                </select>
                <input type="text" class="form-control" name="sourcePath" placeholder="Path or variable name">
                <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                    <i class="bi bi-braces"></i>
                </button>
            </div>
        </div>
        
        <!-- Document ID field (for get action) -->
        <div class="mb-3 document-field" id="document-id-field" style="display: none;">
            <label class="form-label">Document ID</label>
            <div class="input-group">
                <input type="text" class="form-control" name="documentId" placeholder="Enter document ID">
                <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                    <i class="bi bi-braces"></i>
                </button>
            </div>
        </div>
        
        <!-- Document Type field -->
        <div class="mb-3 document-field" id="document-type-field">
            <label class="form-label">Document Type</label>
            <select class="form-control" name="documentType" id="document-type-select">
                <option value="auto">Auto-detect</option>
                <!-- Will be populated from /document/types API -->
            </select>
        </div>

        <!-- NEW: Document Sharing Option -->
        <div class="mb-3 process-field extract-field">
            <label class="form-label">Document Sharing</label>
            <select class="form-control" name="documentSharing" id="document-sharing-select">
                <option value="private">Private - Only use for this workflow</option>
                <option value="share">Share - Available for all agents and users</option>
            </select>
            <small class="form-text text-muted">
                Choose whether this document should be available to other agents and workflows, or kept private for this workflow only.
            </small>
        </div>
        
        <!-- AI Analysis Prompt -->
        <div class="mb-3 document-field" id="prompt-field" style="display: none;">
            <label class="form-label">AI Analysis Prompt</label>
            <div class="input-group">
                <textarea class="form-control" name="prompt" rows="3" placeholder="Describe what you want to analyze in the document"></textarea>
                <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                    <i class="bi bi-braces"></i>
                </button>
            </div>
        </div>
        
        <!-- Page Range (for process) -->
        <div class="mb-3 document-field" id="page-range-field">
            <label class="form-label">Page Range (optional)</label>
            <input type="text" class="form-control" name="pageRange" placeholder="e.g., 1-5, 7, 9-12">
            <small class="form-text text-muted">Leave blank for all pages</small>
        </div>

        <!-- Output Section -->
        <div class="mb-3 document-field" id="output-field">
            <label class="form-label">Output Options</label>
            <div class="input-group">
                <select class="form-control" name="outputType">
                    <option value="variable">Store in Variable</option>
                    <option value="file">Save to File</option>
                    <option value="return">Return as Result</option>
                </select>
                <input type="text" class="form-control" name="outputPath" placeholder="Variable name or file path">
                <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                    <i class="bi bi-braces"></i>
                </button>
            </div>
        </div>

        <!-- Advanced Options Toggle -->
        <div id="advanced-document-options-button" class="mb-3">
            <button type="button" class="btn btn-sm btn-outline-secondary" 
                    onclick="toggleAdvancedOptions()">
                Advanced Options
            </button>
        </div>

        <!-- Advanced Options Section -->
        <div id="advanced-document-options" class="d-none">
            <div class="mb-3">
                <label class="form-label">Processing Options</label>
                <div class="form-check">
                    <input class="form-check-input" type="checkbox" name="forceAiExtraction" id="force-ai" checked disabled>
                    <label class="form-check-label" for="force-ai">Force AI extraction even if templates exist</label>
                </div>
                <div class="form-check">
                    <input class="form-check-input" type="checkbox" name="useBatchProcessing" id="use-batch" checked>
                    <label class="form-check-label" for="use-batch">Use batch processing for multi-page documents</label>
                </div>
                <div class="mb-3 mt-2">
                    <label class="form-label">Batch Size</label>
                    <input type="number" class="form-control" name="batchSize" min="1" max="10" value="3">
                </div>
            </div>
            <div class="mb-3">
                <label class="form-label">Output Format</label>
                <select class="form-control" name="outputFormat">
                    <option value="json">JSON</option>
                    <option value="csv">CSV</option>
                    <option value="text">Text</option>
                </select>
            </div>
        </div>
    `,
    defaultConfig: {
        documentAction: 'process',
        sourceType: 'file',
        sourcePath: '',
        documentId: '',
        documentType: 'auto',
        documentSharing: 'private',  // private or share
        prompt: '',
        outputType: 'variable',
        outputPath: '',
        pageRange: '',
        forceAiExtraction: false,
        useBatchProcessing: true,
        batchSize: 3,
        outputFormat: 'json'
    }
},
'AI Action': {
    template: `
        <div class="mb-3">
            <label for="agent-dropdown" class="font-weight-bold">Select Agent:</label>
            <select id="agent-dropdown" name="agent_id" class="form-control custom-select">
                <option value="">Select an agent</option>
                <!-- Agents options will be populated here dynamically -->
            </select>
        </div>
        <div class="mb-3">
            <label class="form-label">Prompt</label>
            <textarea class="form-control" name="prompt" rows="4" placeholder="Enter your prompt here. You can use $\{variableName\} for workflow variables and {prev_output} for previous step output."></textarea>
            <small class="form-text text-muted">Use $\{variableName\} to insert variables and {prev_output} to include previous step output</small>
        </div>
        <div class="mb-3">
            <label class="form-label">Output Variable</label>
            <div class="input-group">
                <input type="text" class="form-control" name="outputVariable" placeholder="aiResponse">
                <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                    <i class="bi bi-braces"></i>
                </button>
            </div>
            <small class="form-text text-muted">Variable to store the AI response</small>
        </div>
        <div class="mb-3 form-check">
            <input type="checkbox" class="form-check-input" name="continueOnError" id="continueOnError">
            <label class="form-check-label" for="continueOnError">Continue workflow on error</label>
        </div>
    `,
    defaultConfig: {
        agent_id: '',
        prompt: '',
        outputVariable: 'aiResponse',
        continueOnError: false
    }
    },

   'AI Extract': {
        template: `
            <div class="ai-extract-config">
                <!-- Extraction Type (hidden for now) -->
                <div class="mb-3" style="display:none;">
                    <label class="form-label fw-bold">Extraction Type</label>
                    <select class="form-select" name="extractionType" id="ai-extract-type" onchange="AIExtractNode.onExtractionTypeChange(this)">
                        <option value="field_extraction">Field Extraction</option>
                    </select>
                </div>
                
                <!-- Input Source Mode -->
                <div class="mb-3">
                    <label class="form-label fw-bold">Input Source Mode</label>
                    <select class="form-select" name="inputSource" id="ai-extract-input-source">
                        <option value="auto">Auto-detect</option>
                        <option value="text">Raw Text</option>
                        <option value="document">Document File</option>
                    </select>
                    <small class="form-text text-muted">How to interpret the input - auto-detect will check if it's a file path</small>
                </div>
                
                <!-- Single Input Field -->
                <div class="mb-3">
                    <label class="form-label fw-bold">Input</label>
                    <div class="input-group">
                        <input type="text" class="form-control" name="inputVariable" id="ai-extract-input" 
                            list="ai-extract-variables-list" placeholder="\${document.content} or \${selectedFile}">
                        <datalist id="ai-extract-variables-list"></datalist>
                    </div>
                    <small class="form-text text-muted">Variable containing text content or a file path (depending on mode)</small>
                </div>
                
                <!-- Fields Section -->
                <div class="mb-3" id="ai-extract-fields-section">
                    <div class="d-flex justify-content-between align-items-center mb-2">
                        <label class="form-label fw-bold mb-0">Fields to Extract</label>
                        <button type="button" class="btn btn-sm btn-outline-primary" onclick="AIExtractNode.addField()">
                            <i class="bi bi-plus-lg"></i> Add Field
                        </button>
                    </div>
                    
                    <div id="ai-extract-fields-container" class="fields-container"></div>
                    
                    <div id="ai-extract-no-fields" class="text-muted text-center py-3 border rounded">
                        <i class="bi bi-info-circle"></i> No fields defined. Click "Add Field" to start.
                    </div>
                </div>
                
                <!-- Special Instructions -->
                <div class="mb-3">
                    <label class="form-label fw-bold">Special Instructions</label>
                    <textarea class="form-control" name="specialInstructions" id="ai-extract-instructions" 
                            rows="2" placeholder="Optional: Add any special instructions for the AI..." data-no-enhance="true"></textarea>
                    <small class="form-text text-muted">
                        E.g., "Return numbers without currency symbols"
                    </small>
                </div>
                
                <!-- Output Configuration -->
                <div class="mb-3">
                    <label class="form-label fw-bold">Output Variable</label>
                    <div class="input-group">
                        <input type="text" class="form-control" name="outputVariable" id="ai-extract-output" 
                            placeholder="extractedData" value="extractedData">
                    </div>
                    <small class="form-text text-muted">
                        Variable to store extracted data. Access with \${extractedData.fieldName}
                    </small>
                </div>
                
                <!-- Include in Output Options -->
                <div class="mb-3">
                    <label class="form-label fw-bold">Include in Output</label>
                    <small class="form-text text-muted d-block mb-2">Include AI metadata fields in the output variable (e.g., fieldName_confidence)</small>
                    <div class="form-check">
                        <input type="checkbox" class="form-check-input" name="includeConfidence" id="ai-extract-include-confidence">
                        <label class="form-check-label" for="ai-extract-include-confidence">Confidence (LOW/MED/HIGH)</label>
                    </div>
                    <div class="form-check">
                        <input type="checkbox" class="form-check-input" name="includeAssumptions" id="ai-extract-include-assumptions">
                        <label class="form-check-label" for="ai-extract-include-assumptions">Assumptions</label>
                    </div>
                    <div class="form-check">
                        <input type="checkbox" class="form-check-input" name="includeSources" id="ai-extract-include-sources">
                        <label class="form-check-label" for="ai-extract-include-sources">Source Pages</label>
                    </div>
                </div>
                
                <!-- Output Destination -->
                <div class="mb-3">
                    <label class="form-label fw-bold">Output Destination</label>
                    <select class="form-select" name="outputDestination" id="ai-extract-output-destination" onchange="AIExtractNode.onOutputDestinationChange(this.value)">
                        <option value="variable">Variable Only</option>
                        <option value="excel_new">Excel - New File</option>
                        <option value="excel_template">Excel - From Template</option>
                        <option value="excel_append">Excel - Append to Existing</option>
                    </select>
                    <small class="form-text text-muted">Optionally write extraction results to an Excel file</small>
                </div>
                
                <!-- Excel Options Section (collapsible, hidden by default) -->
                <!-- Excel Options Toggle Button (shown when Excel destination selected) -->
                <div id="ai-extract-excel-toggle" class="mb-3" style="display:none;">
                    <button type="button" class="btn btn-outline-success w-100" onclick="AIExtractNode.toggleExcelSlider()">
                        <i class="bi bi-file-earmark-excel me-2"></i> 
                        <span id="ai-extract-excel-toggle-text">Show Excel Options</span>
                        <i class="bi bi-chevron-right ms-2" id="ai-extract-excel-toggle-icon"></i>
                    </button>
                </div>

                <!-- Horizontal Slide-out Excel Options Panel -->
                <div id="ai-extract-excel-slider" class="ai-extract-excel-slider">
                    <div class="slider-header">
                        <span><i class="bi bi-file-earmark-excel me-2"></i>Excel Options</span>
                        <button type="button" class="slider-close" onclick="AIExtractNode.toggleExcelSlider()">×</button>
                    </div>
                    <div class="slider-body">
                        <!-- Excel Output Path -->
                        <div class="mb-3">
                            <label class="form-label fw-bold">Output File Path</label>
                            <input type="text" class="form-control" name="excelOutputPath" id="ai-extract-excel-output-path" 
                                placeholder="/output/$\{name\}_data.xlsx">
                        </div>
                        
                        <!-- Template Path -->
                        <div class="mb-3" id="ai-extract-template-section" style="display:none;">
                            <label class="form-label fw-bold">Template Path</label>
                            <input type="text" class="form-control" name="excelTemplatePath" id="ai-extract-excel-template-path" 
                                placeholder="/templates/template.xlsx">
                        </div>

                        <!-- Sheet Name -->
                        <div class="mb-3">
                            <label class="form-label fw-bold">Sheet Name</label>
                            <input type="text" class="form-control" name="excelSheetName" id="ai-extract-excel-sheet-name" 
                                placeholder="Sheet1 (leave blank for first sheet)">
                        </div>
                        
                        <!-- Column Mapping -->
                        <div id="ai-extract-mapping-section" style="display:none;">
                            <div class="mb-3">
                                <label class="form-label fw-bold">Column Mapping</label>
                                <select class="form-select" name="mappingMode" id="ai-extract-mapping-mode" 
                                        onchange="AIExtractNode.onMappingModeChange(this.value)">
                                    <option value="ai">AI Auto-Mapping</option>
                                    <option value="manual">Manual Mapping</option>
                                </select>
                            </div>
                            
                            <!-- AI Mapping -->
                            <div id="ai-extract-ai-mapping-section">
                                <textarea class="form-control" name="aiMappingInstructions" id="ai-extract-ai-mapping-instructions" 
                                    rows="2" placeholder="Mapping instructions..." data-no-enhance="true"></textarea>
                            </div>
                            
                            <!-- Manual Mapping -->
                            <div id="ai-extract-manual-mapping-section" style="display:none;">
                                <input type="hidden" name="fieldMapping" id="ai-extract-field-mapping">
                                <div id="ai-extract-mapping-container" class="mb-2"></div>
                                <button type="button" class="btn btn-sm btn-outline-secondary" onclick="AIExtractNode.refreshMappingFields()">
                                    <i class="bi bi-arrow-clockwise"></i> Refresh
                                </button>
                            </div>
                        </div>
                        <!-- Formatting Instructions -->
                        <div class="mb-3 mt-3 pt-3 border-top">
                            <label class="form-label fw-bold">
                                <i class="bi bi-palette me-1"></i>Formatting Instructions
                            </label>
                            <textarea class="form-control" name="formattingInstructions" 
                                      id="ai-extract-formatting-instructions" rows="2"
                                      placeholder="e.g., Highlight any values that look suspicious or unusual, mark uncertain extractions in yellow"
                                      data-no-enhance="true"></textarea>
                            <small class="form-text text-muted">
                                Optional: Describe how to format cells. The AI will use its understanding of the document to make intelligent formatting decisions.
                            </small>
                        </div>
                    </div>
                </div>
                
                <!-- Options -->
                <div class="mb-3">
                    <div class="form-check">
                        <input type="checkbox" class="form-check-input" name="failOnMissingRequired" 
                            id="ai-extract-fail-required">
                        <label class="form-check-label" for="ai-extract-fail-required">
                            Fail node if required fields are not found
                        </label>
                    </div>
                </div>
                
                <!-- Output Preview (collapsible) -->
                <div class="mb-3">
                    <div class="d-flex justify-content-between align-items-center">
                        <a class="fw-bold text-decoration-none" data-bs-toggle="collapse" href="#ai-extract-preview-collapse" role="button" aria-expanded="false">
                            <i class="bi bi-chevron-right me-1" id="ai-extract-preview-icon"></i>Output Preview
                        </a>
                        <button type="button" class="btn btn-sm btn-outline-info" onclick="AIExtractNode.testExtraction()">
                            <i class="bi bi-play-fill"></i> Test
                        </button>
                    </div>
                    <div class="collapse" id="ai-extract-preview-collapse">
                        <pre id="ai-extract-preview" class="bg-light border rounded p-2 mt-2 mb-0" 
                            style="max-height: 150px; overflow-y: auto; font-size: 0.8rem;">
                {
                // Add fields to see preview
                }
                        </pre>
                    </div>
                </div>
        `,
        defaultConfig: {
            extractionType: 'field_extraction',
            inputSource: 'auto',
            inputVariable: '',
            outputVariable: 'extractedData',
            specialInstructions: '',
            failOnMissingRequired: false,
            fields: [],
            outputDestination: 'variable',
            excelOutputPath: '',
            excelTemplatePath: '',
            includeAssumptions: false,
            includeSources: false,
            mappingMode: 'ai',
            fieldMapping: null,
            aiMappingInstructions: '',
            formattingInstructions: ''
        }
    },

    'Folder Selector': {
        template: `
        <div class="mb-3">
            <label class="form-label">Folder Path</label>
            <div class="input-group">
                <input type="text" class="form-control" name="folderPath" placeholder="/path/to/folder">
                <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                    <i class="bi bi-braces"></i>
                </button>
            </div>
            <small class="form-text text-muted">Path to the folder containing files to select from</small>
        </div>
        <div class="mb-3">
            <label class="form-label">Selection Mode</label>
            <select class="form-control" name="selectionMode" onchange="toggleFileSelectionOptions(this)">
                <option value="all">All Files</option>
                <option value="pattern">File Matching Pattern</option>
                <option value="first">First File</option>
                <option value="latest">Latest Modified File</option>
                <option value="largest">Largest File</option>
                <option value="smallest">Smallest File</option>
                <option value="random">Random File</option>
            </select>
        </div>
        <div class="mb-3" id="pattern-option" style="display: none;">
            <label class="form-label">File Pattern</label>
            <div class="input-group">
                <input type="text" class="form-control" name="filePattern" placeholder="*.pdf">
                <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                    <i class="bi bi-braces"></i>
                </button>
            </div>
            <small class="form-text text-muted">Pattern like *.pdf or invoice_*.csv (use * as wildcard)</small>
        </div>
        <div class="mb-3">
            <label class="form-label">Output Variable</label>
            <div class="input-group">
                <input type="text" class="form-control" name="outputVariable" placeholder="selectedFile">
                <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                    <i class="bi bi-braces"></i>
                </button>
            </div>
            <small class="form-text text-muted">Variable to store the selected file path</small>
        </div>
        <div class="mb-3 form-check">
            <input type="checkbox" class="form-check-input" name="failIfEmpty" id="failIfEmpty" checked>
            <label class="form-check-label" for="failIfEmpty">Fail workflow if no files found</label>
        </div>
    `,
    defaultConfig: {
        folderPath: '',
        selectionMode: 'first',
        filePattern: '*.*',
        outputVariable: '',
        failIfEmpty: true
    }
    },
    'Human Approval': {
        template: `
            <!-- Two column layout for compact form -->
            <div class="row">
                <!-- Left Column -->
                <div class="col-6">
                    <div class="mb-2">
                        <label class="form-label small mb-1">Title <span class="text-danger">*</span></label>
                        <input type="text" class="form-control form-control-sm" name="approvalTitle" 
                            placeholder="e.g., Budget Approval" required>
                    </div>
                    
                    <div class="mb-2">
                        <label class="form-label small mb-1">Assign To <span class="text-danger">*</span></label>
                        <select class="form-select form-select-sm" name="assigneeType" id="assigneeType" required>
                            <option value="">-- Select --</option>
                            <option value="user">Specific User</option>
                            <option value="group">User Group</option>
                            <option value="unassigned">Available to All</option>
                        </select>
                    </div>
                    
                    <div class="mb-2" id="assigneeSelectGroup" style="display: none;">
                        <label class="form-label small mb-1">Select <span class="text-danger">*</span></label>
                        <select class="form-select form-select-sm" name="assigneeId" id="assigneeId">
                            <option value="">-- Select --</option>
                        </select>
                    </div>
                    
                    <div class="mb-2">
                        <label class="form-label small mb-1">Priority</label>
                        <select class="form-select form-select-sm" name="priority">
                            <option value="0">Normal</option>
                            <option value="1">High</option>
                            <option value="2">Urgent</option>
                        </select>
                    </div>
                </div>
                
                <!-- Right Column -->
                <div class="col-6">
                    <div class="mb-2">
                        <label class="form-label small mb-1">Timeout (hours)</label>
                        <input type="number" class="form-control form-control-sm" name="dueHours" 
                            placeholder="No timeout" min="0" step="0.5">
                        <input type="hidden" name="timeoutMinutes" id="timeoutMinutes" value="60">
                    </div>
                    
                    <div class="mb-2">
                        <label class="form-label small mb-1">Timeout Action</label>
                        <select class="form-select form-select-sm" name="timeoutAction">
                            <option value="continue">Auto-approve</option>
                            <option value="fail">Fail workflow</option>
                        </select>
                    </div>
                    
                    <div class="mb-2">
                        <label class="form-label small mb-1">Description</label>
                        <textarea class="form-control form-control-sm" name="approvalDescription" rows="2" 
                                placeholder="Brief context..."></textarea>
                    </div>
                </div>
            </div>
            
            <!-- Full width for approval data -->
            <div class="mb-2">
                <label class="form-label small mb-1">Approval Data</label>
                <textarea class="form-control form-control-sm" name="approvalData" rows="2" 
                        placeholder="Additional data or \${variableName}"></textarea>
            </div>
            
            <!-- Hidden backward compatibility -->
            <input type="hidden" name="assignee" value="">
            
            <!-- Compact info box -->
            <div class="alert alert-info py-1 px-2 small mb-0">
                <strong>Connections:</strong> PASS (approved) • FAIL (rejected) • COMPLETE (always)
            </div>
        `,
        defaultConfig: {
            approvalTitle: 'Approval Required',
            approvalDescription: '',
            assignee: '',  // Backward compatibility
            assigneeType: '',
            assigneeId: '',
            priority: 0,
            dueHours: '',
            timeoutMinutes: 720,
            timeoutAction: 'continue',
            approvalData: '{}'
        }
    },
    'Conditional': {
        template: `
            <div class="mb-3">
                <label class="form-label">Condition Type</label>
                <select class="form-control" name="conditionType" onchange="updateConditionFields(this.value)">
                    <option value="comparison">Variable Comparison</option>
                    <option value="expression">Python Expression</option>
                    <option value="contains">Text Contains</option>
                    <option value="exists">Variable Exists</option>
                    <option value="empty">Is Empty</option>
                </select>
            </div>
            
            <!-- Variable Comparison Fields -->
            <div id="comparison-fields">
                <div class="mb-3">
                    <label class="form-label">Left Value</label>
                    <div class="input-group">
                        <input type="text" class="form-control" name="leftValue" placeholder="Variable name or value">
                        <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                            <i class="bi bi-braces"></i>
                        </button>
                    </div>
                    <small class="form-text text-muted">Use \${varName} for variables or enter a direct value</small>
                </div>

                <div class="mb-3">
                    <label class="form-label">Operator</label>
                    <select class="form-control" name="operator">
                        <option value="==">Equals (==)</option>
                        <option value="!=">Not Equals (!=)</option>
                        <option value=">">Greater Than (>)</option>
                        <option value=">=">Greater Than or Equal (>=)</option>
                        <option value="<">Less Than (<)</option>
                        <option value="<=">Less Than or Equal (<=)</option>
                    </select>
                </div>

                <div class="mb-3">
                    <label class="form-label">Right Value</label>
                    <div class="input-group">
                        <input type="text" class="form-control" name="rightValue" placeholder="Variable name or value">
                        <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                            <i class="bi bi-braces"></i>
                        </button>
                    </div>
                    <small class="form-text text-muted">Use \${varName} for variables or enter a direct value</small>
                </div>
            </div>
            
            <!-- Expression Field -->
            <div id="expression-field" style="display: none;">
                <div class="mb-3">
                    <label class="form-label">Python Expression</label>
                    <div class="input-group">
                        <textarea class="form-control" name="expression" rows="3"
                            placeholder="Example: len(\${file_paths}) > 0 or \${status} == 'active'"></textarea>
                        <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                            <i class="bi bi-braces"></i>
                        </button>
                    </div>
                    <small class="form-text text-muted">
                        Write a Python expression that evaluates to True or False.
                        Access variables with \${varName} syntax. Functions available: len(), str(), int(), max(), min(), sum(), etc.
                    </small>
                </div>
            </div>
            
            <!-- Contains Fields -->
            <div id="contains-fields" style="display: none;">
                <div class="mb-3">
                    <label class="form-label">Text/Variable to Check</label>
                    <div class="input-group">
                        <input type="text" class="form-control" name="containsText" placeholder="Variable or text">
                        <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                            <i class="bi bi-braces"></i>
                        </button>
                    </div>
                </div>
                <div class="mb-3">
                    <label class="form-label">Search For</label>
                    <div class="input-group">
                        <input type="text" class="form-control" name="searchText" placeholder="Text to search for">
                        <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                            <i class="bi bi-braces"></i>
                        </button>
                    </div>
                </div>
            </div>

            <!-- Exists Field -->
            <div id="exists-field" style="display: none;">
                <div class="mb-3">
                    <label class="form-label">Variable Name</label>
                    <select class="form-control" name="existsVariable">
                        <option value="">Select a variable...</option>
                        <!-- Variables will be populated here -->
                    </select>
                </div>
            </div>
            
            <!-- Empty Field -->
            <div id="empty-field" style="display: none;">
                <div class="mb-3">
                    <label class="form-label">Variable to Check</label>
                    <div class="input-group">
                        <input type="text" class="form-control" name="emptyVariable" placeholder="Variable name">
                        <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                            <i class="bi bi-braces"></i>
                        </button>
                    </div>
                </div>
            </div>
            
            <div class="alert alert-info">
                <i class="bi bi-info-circle"></i> 
                Connect the <strong>Pass (Right)</strong> endpoint for True conditions and 
                <strong>Fail (Left)</strong> endpoint for False conditions.
            </div>
        `,
        defaultConfig: {
            conditionType: 'comparison',
            leftValue: '',
            operator: '==',
            rightValue: '',
            expression: '',
            containsText: '',
            searchText: '',
            existsVariable: '',
            emptyVariable: ''
        }
    },
    'Loop': {
    template: `
        <div class="mb-3">
            <label class="form-label">Loop Source Type</label>
            <select class="form-control" name="sourceType" onchange="updateLoopSourceFields(this.value)">
                <option value="auto">Auto-Detect (Recommended)</option>
                <option value="variable">Variable</option>
                <option value="path">Output Path</option>
                <option value="folderFiles">Folder Selector Files</option>
                <option value="split">Split String</option>
            </select>
            <small class="form-text text-muted">
                Auto-detect will intelligently find arrays from the previous node
            </small>
        </div>
        
        <div id="loop-source-group" class="mb-3">
            <label class="form-label">Loop Source</label>
            <div class="input-group">
                <input type="text" class="form-control" name="loopSource" 
                       placeholder="Leave empty for auto-detect, or specify source">
            </div>
            <small class="form-text text-muted">
                Examples: \${myArray}, data.results, or leave empty to auto-detect
            </small>
        </div>
        
        <div id="split-config-group" class="mb-3" style="display: none;">
            <label class="form-label">Split Delimiter</label>
            <input type="text" class="form-control" name="splitDelimiter" 
                   value="," placeholder="Delimiter to split string">
            <small class="form-text text-muted">
                Split a string into an array using this delimiter (e.g., comma, newline)
            </small>
        </div>
        
        <div class="mb-3">
            <label class="form-label">Current Item Variable</label>
            <input type="text" class="form-control" name="itemVariable" 
                   value="currentItem" placeholder="Variable name for current item">
        </div>
        
        <div class="mb-3">
            <label class="form-label">Current Index Variable</label>
            <input type="text" class="form-control" name="indexVariable" 
                   value="currentIndex" placeholder="Variable name for current index">
        </div>
        
        <div class="mb-3">
            <label class="form-label">Array Info Variable (Optional)</label>
            <input type="text" class="form-control" name="arrayInfoVariable" 
                   placeholder="Variable to store array info (length, etc.)">
            <small class="form-text text-muted">
                Stores: {items: [...], length: n, source: "..."}
            </small>
        </div>
        
        <div class="mb-3">
            <label class="form-label">Maximum Iterations</label>
            <input type="number" class="form-control" name="maxIterations" 
                   value="100" min="1" max="10000">
        </div>
        
        <div class="mb-3">
            <label class="form-label">Empty Array Behavior</label>
            <select class="form-control" name="emptyBehavior">
                <option value="skip">Skip (Continue)</option>
                <option value="fail">Fail (Stop Workflow)</option>
                <option value="default">Use Default Value</option>
            </select>
        </div>
        
        <div id="default-value-group" class="mb-3" style="display: none;">
            <label class="form-label">Default Array (JSON)</label>
            <textarea class="form-control" name="defaultArray" rows="2" 
                      placeholder='["default1", "default2"]'></textarea>
        </div>
        
        <div class="mb-3">
            <label class="form-label">Output Mode</label>
            <select class="form-control" name="outputMode">
                <option value="array">Collect All Results</option>
                <option value="last">Last Result Only</option>
                <option value="concat">Concatenate Strings</option>
                <option value="merge">Merge Objects</option>
                <option value="none">No Collection</option>
            </select>
        </div>
        
        <div class="alert alert-info">
            <i class="bi bi-lightbulb"></i> <strong>Auto-Detection:</strong><br>
            • <strong>Folder Selector:</strong> Automatically uses allFiles<br>
            • <strong>Database:</strong> Uses query results array<br>
            • <strong>Set Variable:</strong> Uses variable value if it's an array<br>
            • <strong>Any node:</strong> Searches for first array in output
        </div>
    `,
    defaultConfig: {
        sourceType: 'auto',
        loopSource: '',
        itemVariable: 'currentItem',
        indexVariable: 'currentIndex',
        arrayInfoVariable: '',
        maxIterations: 100,
        outputMode: 'array',
        emptyBehavior: 'skip',
        defaultArray: '[]',
        splitDelimiter: ','
    }
    },
    'End Loop': {
        template: `
            <div class="mb-3">
                <label class="form-label">Associated Loop</label>
                <select class="form-control" name="loopNodeId">
                    <option value="">Auto-detect (nearest Loop node)</option>
                    <!-- Will be populated with Loop nodes in the workflow -->
                </select>
                <small class="form-text text-muted">
                    Select which Loop this End Loop is associated with, or leave on auto-detect
                </small>
            </div>
            
            <div class="mb-3">
                <label class="form-label">Completion Message (Optional)</label>
                <div class="input-group">
                    <input type="text" class="form-control" name="completionMessage" 
                        placeholder="e.g., Processed \${_loopStats.processedItems} items">
                    <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                        <i class="bi bi-braces"></i>
                    </button>
                </div>
                <small class="form-text text-muted">
                    Optional message to log when loop completes
                </small>
            </div>
            
            <div class="alert alert-warning">
                <i class="bi bi-exclamation-triangle"></i> 
                <strong>Important:</strong><br>
                • Place this node at the end of your loop body<br>
                • Nodes connected after End Loop execute ONCE after all iterations<br>
                • The loop will pass its accumulated results to the next nodes
            </div>
        `,
        defaultConfig: {
            loopNodeId: '',
            completionMessage: ''
        }
    },
    'Execute Application': {
        template: `
            <!-- Command Configuration -->
            <div class="row">
                <div class="col-md-6 mb-2">
                    <label class="form-label small">Command Type</label>
                    <select class="form-control form-control-sm" name="commandType" id="command-type-select">
                        <option value="executable">Executable</option>
                        <option value="script">Script</option>
                        <option value="command">System Command</option>
                    </select>
                </div>
                <div class="col-md-6 mb-2">
                    <label class="form-label small">Timeout (seconds)</label>
                    <input type="number" class="form-control form-control-sm" name="timeout" 
                        value="300" min="1" max="3600">
                </div>
            </div>
            
            <!-- Path/Command -->
            <div class="mb-2">
                <label class="form-label small">Path/Command <span class="text-danger">*</span></label>
                <div class="input-group input-group-sm">
                    <input type="text" class="form-control" name="executablePath" 
                        placeholder="/path/to/executable, script.py, or command" required>
                    <button type="button" class="btn btn-outline-secondary btn-sm" onclick="showVariableSelector(this)">
                        <i class="bi bi-braces"></i>
                    </button>
                </div>
            </div>
            
            <!-- Arguments -->
            <div class="mb-2">
                <label class="form-label small">Arguments</label>
                <div class="input-group input-group-sm">
                    <textarea class="form-control" name="arguments" rows="2" 
                            placeholder="arg1 arg2 or one per line"></textarea>
                    <button type="button" class="btn btn-outline-secondary btn-sm" onclick="showVariableSelector(this)">
                        <i class="bi bi-braces"></i>
                    </button>
                </div>
            </div>
            
            <!-- Two column section -->
            <div class="row">
                <!-- Left Column -->
                <div class="col-md-6">
                    <div class="mb-2">
                        <label class="form-label small">Working Directory</label>
                        <div class="input-group input-group-sm">
                            <input type="text" class="form-control" name="workingDirectory" 
                                placeholder="/path/to/dir">
                            <button type="button" class="btn btn-outline-secondary btn-sm" onclick="showVariableSelector(this)">
                                <i class="bi bi-braces"></i>
                            </button>
                        </div>
                    </div>
                    
                    <div class="mb-2">
                        <label class="form-label small">Input Data</label>
                        <select class="form-control form-control-sm" name="inputDataHandling">
                            <option value="none">None</option>
                            <option value="stdin">STDIN</option>
                            <option value="file">Temp File</option>
                            <option value="args">As Args</option>
                        </select>
                    </div>
                    
                    <div class="mb-2">
                        <label class="form-label small">Success Codes</label>
                        <input type="text" class="form-control form-control-sm" name="successCodes" 
                            value="0" placeholder="0,1,2">
                    </div>
                </div>
                
                <!-- Right Column -->
                <div class="col-md-6">
                    <div class="mb-2">
                        <label class="form-label small">Output Variable</label>
                        <div class="input-group input-group-sm">
                            <input type="text" class="form-control" name="outputVariable" 
                                placeholder="varName">
                            <button type="button" class="btn btn-outline-secondary btn-sm" onclick="showVariableSelector(this)">
                                <i class="bi bi-braces"></i>
                            </button>
                        </div>
                    </div>
                    
                    <div class="mb-2">
                        <label class="form-label small">Output Format</label>
                        <select class="form-control form-control-sm" name="outputParsing" onchange="toggleOutputRegexField()">
                            <option value="text">Text</option>
                            <option value="json">JSON</option>
                            <option value="csv">CSV</option>
                            <option value="xml">XML</option>
                            <option value="regex">Regex</option>
                        </select>
                    </div>
                    
                    <div class="mb-2" id="output-regex-group" style="display:none;">
                        <label class="form-label small">Regex Pattern</label>
                        <input type="text" class="form-control form-control-sm" name="outputRegex" placeholder=".*">
                    </div>
                </div>
            </div>
            
            <!-- Environment Variables (collapsible) -->
            <div class="mb-2">
                <a class="small text-decoration-none" data-bs-toggle="collapse" href="#envVarsCollapse" role="button">
                    <i class="bi bi-chevron-right"></i> Environment Variables
                </a>
                <div class="collapse" id="envVarsCollapse">
                    <textarea class="form-control form-control-sm mt-1" name="environmentVars" rows="2" 
                            placeholder="KEY1=value1&#10;KEY2=value2"></textarea>
                </div>
            </div>
            
            <!-- Options checkboxes in compact row -->
            <div class="row mb-2">
                <div class="col-md-4">
                    <div class="form-check form-check-sm">
                        <input class="form-check-input" type="checkbox" name="captureOutput" 
                            id="capture-output" checked>
                        <label class="form-check-label small" for="capture-output">
                            Capture Output
                        </label>
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="form-check form-check-sm">
                        <input class="form-check-input" type="checkbox" name="failOnError" 
                            id="fail-on-error" checked>
                        <label class="form-check-label small" for="fail-on-error">
                            Fail on Error
                        </label>
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="form-check form-check-sm">
                        <input class="form-check-input" type="checkbox" name="continueOnError" 
                            id="continue-on-error">
                        <label class="form-check-label small" for="continue-on-error">
                            Continue on Error
                        </label>
                    </div>
                </div>
            </div>
            
            <!-- Compact security note -->
            <div class="alert alert-info alert-sm py-1 px-2 mb-0">
                <small>
                    <i class="bi bi-shield-check"></i> 
                    Only whitelisted commands can be executed for security.
                </small>
            </div>
            
            <style>
                #envVarsCollapse .collapse.show ~ i.bi-chevron-right::before {
                    transform: rotate(90deg);
                }
                .form-check-sm .form-check-input {
                    margin-top: 0.1em;
                }
                .alert-sm {
                    font-size: 0.875rem;
                }
            </style>
        `,
        defaultConfig: {
            commandType: 'command',
            executablePath: '',
            arguments: '',
            workingDirectory: '',
            environmentVars: '',
            timeout: 300,
            captureOutput: true,
            successCodes: '0',
            failOnError: true,
            inputDataHandling: 'none',
            outputParsing: 'text',
            outputRegex: ''
        }
    }
};

// Initialize the modal when the document loads
// document.addEventListener('DOMContentLoaded', function() {
//     configModal = new bootstrap.Modal(document.getElementById('nodeConfigModal'));  // TODO does this code even do anything??? The dom load cannot be called twice, right?
// });

// Function to rename a node
function renameNode() {
    if (selectedNode) {
        const contentElement = selectedNode.querySelector('.node-content');
        const currentName = contentElement.textContent.trim();
        const newName = prompt('Enter new name:', currentName);
        
        if (newName && newName.trim()) {
            contentElement.innerHTML = `
                <i class="${contentElement.querySelector('i').className}"></i> 
                ${newName.trim()}
            `;
        }
        
        // Hide the context menu
        const nodeMenu = document.getElementById('node-context-menu');
        if (nodeMenu) {
            nodeMenu.style.display = 'none';
        }
    }
}

// Function to open configuration modal
function configureNode() {
    if (selectedNode) {
        console.log('Selected Node: ' + selectedNode.id);
        // Store the node being configured
        configuredNode = selectedNode;
        const nodeType = selectedNode.getAttribute('data-type');
        const configTemplate = nodeConfigTemplates[nodeType];

        console.log('Node Type: ' + nodeType);
        
        if (configTemplate) {
            // Get existing config or use default
            const currentConfig = nodeConfigs.get(selectedNode.id) || {...configTemplate.defaultConfig};
            
            // Set modal title
            document.getElementById('nodeConfigModalLabel').textContent = 
                `Configure ${selectedNode.querySelector('.node-content').textContent.trim()}`;
            
            // Set modal body
            const modalBody = document.getElementById('nodeConfigModalBody');
            modalBody.innerHTML = configTemplate.template;

            // Load user/group dropdowns for Human Approval nodes
            if (configuredNode.getAttribute('data-type') === 'Human Approval') {
                setTimeout(() => {
                    setupHumanApprovalHandlers();
                    
                    // If editing existing node, restore values and trigger change
                    const config = nodeConfigs.get(configuredNode.id) || {};
                    if (config.assigneeType) {
                        const typeSelect = document.getElementById('assigneeType');
                        if (typeSelect) {
                            typeSelect.value = config.assigneeType;
                            typeSelect.dispatchEvent(new Event('change'));
                            
                            // Set the assigneeId after options load
                            setTimeout(() => {
                                if (config.assigneeId) {
                                    const idSelect = document.getElementById('assigneeId');
                                    if (idSelect) {
                                        idSelect.value = config.assigneeId;
                                    }
                                }
                            }, 500);
                        }
                    }
                }, 100);
            }

            // TODO: Finish customizing tool configs
            // Populate config values
            console.log('nodeType:' + nodeType);
            if (nodeType == 'Database') {
                console.log('Loading database connections...');
                populateExistingConnections(connData);
            } else if (nodeType == 'AI Action') {
                console.log('Loading database agents...');
                populateExistingAgents(agentData);
            } else if (nodeType == 'Folder Selector') {
                // Initialize the file selection options visibility
                const selectionMode = currentConfig.selectionMode || 'first';
                const patternDiv = document.getElementById('pattern-option');
                if (patternDiv) {
                    patternDiv.style.display = selectionMode === 'pattern' ? 'block' : 'none';
                }
            } else if (nodeType == 'Set Variable') {
                console.log(`Set Var Config: ${formatJsonOutput(currentConfig)}`);
                const patternDiv = document.getElementById('pattern-option');
                if (currentConfig.valueSource === "valueSource") {

                }
            } else if (nodeType == 'AI Extract') {
                console.log('Initializing AI Extract node...');
                // Initialize AI Extract module
                if (typeof AIExtractNode !== 'undefined') {
                    setTimeout(() => {
                        AIExtractNode.init();
                        // Load existing config if any
                        if (currentConfig && currentConfig.fields && currentConfig.fields.length > 0) {
                            AIExtractNode.loadConfig(currentConfig);
                        }
                    }, 100);
                }
            } else if (nodeType == 'Excel Export') {
                console.log('Initializing Excel Export node...');
                // Initialize Excel Export module
                if (typeof ExcelExportNode !== 'undefined') {
                    setTimeout(() => {
                        ExcelExportNode.init();
                        // Load existing config if any
                        if (currentConfig) {
                            ExcelExportNode.loadConfig(currentConfig);
                        }
                    }, 100);
                }
            }

                        // For Folder Selector, populate the variable dropdown for output variable
                        if (nodeType === 'Folder Selector' || nodeType === 'Set Variable') {
                            console.log(`Populating variable dropdown...`);
                            populateVariableDropdown();
                        }
            
            // Fill in existing values
            Object.entries(currentConfig).forEach(([key, value]) => {
                const input = modalBody.querySelector(`[name="${key}"]`);
                if (input) {
                    console.log('Setting control name: ' + input.name);
                    console.log('Setting control value: ' + value);
                    
                    if (input.type === 'checkbox') {
                        input.checked = value;
                    } else if (input.type === 'radio') {
                        // For radio buttons, we need to find the one with matching value
                        const radioWithMatchingValue = modalBody.querySelector(`input[name="${key}"][value="${value}"]`);
                        if (radioWithMatchingValue) {
                            radioWithMatchingValue.checked = true;
                        }
                    } else if (input.tagName === 'SELECT') {
                        // For select elements, find and set the matching option
                        const option = Array.from(input.options).find(opt => opt.value === value);
                        if (option) {
                            input.value = value;
                        } else if (value && input.options.length > 0) {
                            // If the exact value isn't found but we have a value and options exist
                            console.log('Select option with value "' + value + '" not found');
                        }
                    } else {
                        input.value = value;
                    }
                } else if (input === null) {
                    // Handle case where we're dealing with a radio group
                    const radioInput = modalBody.querySelector(`input[type="radio"][name="${key}"][value="${value}"]`);
                    if (radioInput) {
                        radioInput.checked = true;
                    }
                }
            });

            // After populating form values, trigger field visibility updates for nodes with dynamic fields
            if (nodeType === 'Database') {
                toggleDbOperationFields();
            } else if (nodeType === 'Conditional') {
                const conditionType = currentConfig.conditionType || 'comparison';
                updateConditionFields(conditionType);
            } else if (nodeType === 'Alert') {
                const alertType = currentConfig.alertType || 'email';
                updateAlertFields(alertType);
            }

            // Show modal
            configModal.show();
        }
        
        // Hide the context menu
        const nodeMenu = document.getElementById('node-context-menu');
        if (nodeMenu) {
            nodeMenu.style.display = 'none';
        }
    }
}

// Function to save node configuration
function saveNodeConfig() {
    console.log('Saving node config...');
    if (configuredNode) {
        console.log('Selected Node: ' + configuredNode);        
        const nodeType = configuredNode.getAttribute('data-type');
        const modalBody = document.getElementById('nodeConfigModalBody');
        const config = {};
        
        // Track which radio button groups we've already processed
        const processedRadioGroups = new Set();
        
        // Collect all form values
        const inputs = modalBody.querySelectorAll('input, select, textarea');
        inputs.forEach(input => {
            console.log('Input name: ' + input.name);
            
            if (input.type === 'checkbox') {
                console.log('Saving checkbox state: ' + input.checked);
                config[input.name] = input.checked;
            } else if (input.type === 'radio') {
                // Only process each radio group once
                if (!processedRadioGroups.has(input.name)) {
                    processedRadioGroups.add(input.name);
                    
                    // Find the selected radio in this group
                    const selectedRadio = modalBody.querySelector(`input[name="${input.name}"]:checked`);
                    if (selectedRadio) {
                        console.log(`Saving radio group ${input.name} value: ${selectedRadio.value}`);
                        config[input.name] = selectedRadio.value;
                    } else {
                        console.log(`No selection in radio group ${input.name}`);
                        config[input.name] = '';  // Or set a default value
                    }
                }
            } else {
                console.log('Saving config input: ' + input.value);
                config[input.name] = input.value;
            }
        });

        // Special handling for AI Extract - get fields from module
        if (nodeType === 'AI Extract' && typeof AIExtractNode !== 'undefined') {
            const extractConfig = AIExtractNode.getConfig();
            // Merge the fields array into the config
            config.fields = extractConfig.fields;
            config.extractionType = extractConfig.extractionType;
            config.inputVariable = extractConfig.inputVariable;
            config.outputVariable = extractConfig.outputVariable;
            config.specialInstructions = extractConfig.specialInstructions;
            config.failOnMissingRequired = extractConfig.failOnMissingRequired;
        }
        
        // Special handling for Excel Export - get config from module
        if (nodeType === 'Excel Export' && typeof ExcelExportNode !== 'undefined') {
            const exportConfig = ExcelExportNode.getConfig();
            // Merge all config properties
            Object.assign(config, exportConfig);
        }
        
        console.log(`Saving node config: ${formatJsonOutput(config)}`);

        // Save configuration
        nodeConfigs.set(configuredNode.id, config);
        
        // Hide modal
        configModal.hide();
    }
}


function createNode(type, x, y) {
    console.log(`Creating node of type "${type}" at target position (${x}, ${y})`);
    
    // Create node element
    const node = document.createElement('div');
    node.className = 'workflow-node';
    node.id = `node-${nodeCounter++}`;
    node.setAttribute('data-type', type);
    
    // Get the canvas
    const canvas = document.getElementById('workflow-canvas');
    
    // Set position BEFORE adding to DOM
    node.style.position = 'absolute';
    node.style.left = `${x}px`;
    node.style.top = `${y}px`;
    
    // Create content container
    const contentContainer = document.createElement('div');
    contentContainer.className = 'node-content';
    
    // Add icon based on type
    const icon = document.createElement('i');
    switch(type) {
        case 'Database':
            icon.className = 'bi bi-database';
            break;
        case 'File':
            icon.className = 'bi bi-file-earmark';
            break;
        case 'Server':
            icon.className = 'bi bi-server';
            break;
        case 'Alert':
            icon.className = 'bi bi-exclamation-triangle';
            break;
        case 'Document':
            icon.className = 'bi bi-file-text';
            break;
        case 'AI Action':
            icon.className = 'bi bi-robot';
            break;
        case 'Folder Selector':
            icon.className = 'bi bi-folder2';
            break;
        case 'Set Variable':
            icon.className = 'bi bi-braces';
            break;
        case 'Human Approval':
            icon.className = 'bi bi-person-check';
            break;
        case 'Conditional':
            icon.className = 'bi bi-diagram-3';
            break;
        case 'Loop':
            icon.className = 'bi bi-arrow-repeat';
            break;
        case 'End Loop':
            icon.className = 'bi bi-arrow-return-left';
            break;
        case 'Execute Application':
            icon.className = 'bi bi-terminal';
            break;
        case 'AI Extract':
            icon.className = 'bi bi-search';
            break;
        case 'Excel Export':
            icon.className = 'bi bi-file-earmark-excel';
            break;
        default:
            icon.className = 'bi bi-box';
    }
    
    // contentContainer.appendChild(icon);
    // contentContainer.appendChild(document.createTextNode(' ' + (type === 'Loop' ? 'Start Loop' : type)));
    // node.appendChild(contentContainer);

    // Truncate label if longer than 20 characters
    contentContainer.appendChild(icon);
    let labelText = type;
    if (labelText.length > 20) {
        labelText = labelText.substring(0, 20) + '...';
    }
    contentContainer.appendChild(document.createTextNode(' ' + labelText));
    node.appendChild(contentContainer);

    // Add endpoint indicators
    const leftEndpoint = document.createElement('div');
    leftEndpoint.className = 'endpoint left-endpoint';
    node.appendChild(leftEndpoint);

    const rightEndpoint = document.createElement('div');
    rightEndpoint.className = 'endpoint right-endpoint';
    node.appendChild(rightEndpoint);

    // Initialize node configuration
    if (nodeConfigTemplates[type]) {
        nodeConfigs.set(node.id, {...nodeConfigTemplates[type].defaultConfig});
    }
    
    // Add to canvas
    canvas.appendChild(node);
    
    // Make the node draggable with custom drag handler
    jsPlumbInstance.draggable(node, {
        grid: [10, 10],
        // Use a custom drag handler to prevent transform issues
        drag: function(params) {
            // Update position using left/top instead of transform
            node.style.left = params.pos[0] + 'px';
            node.style.top = params.pos[1] + 'px';
            
            // Prevent jsPlumb from applying transforms
            node.style.transform = 'none';
            
            // Force update of endpoints
            jsPlumbInstance.revalidate(node);
        },
        start: function(params) {
            console.log('Drag start position:', {
                left: node.style.left,
                top: node.style.top
            });
            
            // Ensure no transform at start
            node.style.transform = 'none';
        },
        stop: function(params) {
            // Final position update
            node.style.left = params.pos[0] + 'px';
            node.style.top = params.pos[1] + 'px';
            node.style.transform = 'none';
            
            console.log('Drag stop position:', {
                left: node.style.left,
                top: node.style.top
            });
            
            // Force jsPlumb to repaint connections
            jsPlumbInstance.repaintEverything();
        }
    });
    
    // Add endpoints after positioning is finalized
    jsPlumbInstance.addEndpoint(node, {
        anchor: "Right",
        isSource: true,
        isTarget: true,
        maxConnections: -1,
        connectionType: "basic",
        endpoint: "Dot",
        endpointStyle: { fill: "#456" }
    });

    jsPlumbInstance.addEndpoint(node, {
        anchor: "Left",
        isSource: true,
        isTarget: true,
        maxConnections: -1,
        connectionType: "basic",
        endpoint: "Dot",
        endpointStyle: { fill: "#456" }
    });

    // Add endpoint hover effects
    node.addEventListener('mouseenter', () => {
        const leftEp = node.querySelector('.left-endpoint');
        const rightEp = node.querySelector('.right-endpoint');
        if (leftEp) leftEp.style.opacity = '1';
        if (rightEp) rightEp.style.opacity = '1';
    });

    node.addEventListener('mouseleave', () => {
        const leftEp = node.querySelector('.left-endpoint');
        const rightEp = node.querySelector('.right-endpoint');
        if (leftEp) leftEp.style.opacity = '0';
        if (rightEp) rightEp.style.opacity = '0';
    });
    
    console.log('Final node position:', {
        left: node.style.left,
        top: node.style.top,
        offsetLeft: node.offsetLeft,
        offsetTop: node.offsetTop
    });

    return node;  // FOR WORKFLOW AI ASSISTANT ONLY
}



function setupContextMenus() {
    const arrowMenu = document.getElementById('arrow-context-menu');
    const nodeMenu = document.getElementById('node-context-menu');
    
    // Hide context menus when clicking outside
    document.addEventListener('click', (e) => {
        let targetElement = e.target;
        let isContextMenu = false;
        
        while (targetElement != null) {
            if (targetElement.classList && targetElement.classList.contains('context-menu')) {
                isContextMenu = true;
                break;
            }
            targetElement = targetElement.parentElement;
        }
        
        if (!isContextMenu) {
            arrowMenu.style.display = 'none';
            nodeMenu.style.display = 'none';
            selectedConnection = null;
            selectedNode = null;
        }
    });

    // Handle context menu for connections
    function handleConnectionContextMenu(connection, e) {
        if (!e) return;
        
        e.preventDefault();
        e.stopPropagation();
        
        selectedConnection = connection;
        selectedNode = null;
        
        arrowMenu.style.display = 'block';
        arrowMenu.style.left = `${e.pageX}px`;
        arrowMenu.style.top = `${e.pageY}px`;
    }
    
    // Helper function to bind context menu to a connection's canvas element
    function bindContextMenuToConnection(connection) {
        if (connection && connection.canvas) {
            // Remove any existing listener to avoid duplicates
            const newHandler = (e) => {
                handleConnectionContextMenu(connection, e);
            };
            
            // Store the handler so we can remove it if needed
            connection.canvas._contextMenuHandler = newHandler;
            
            // Add the context menu listener to the canvas element
            connection.canvas.addEventListener('contextmenu', newHandler);
        }
    }
    
    // Bind to jsPlumb's connection event to capture new connections
    jsPlumbInstance.bind('connection', function(info) {
        // Set default connection type to pass
        setArrowType('pass', info.connection);
        
        // Store the original anchors in the connection data
        const sourceAnchor = info.connection.endpoints[0].anchor.type;
        const targetAnchor = info.connection.endpoints[1].anchor.type;
        info.connection.setData({
            type: 'pass',
            sourceAnchor: sourceAnchor,
            targetAnchor: targetAnchor
        });
        
        // IMPORTANT FIX: Use the canvas element method for new connections
        // This ensures the context menu works immediately after creation
        bindContextMenuToConnection(info.connection);
    });
    
    // For existing connections (when loading a workflow)
    jsPlumbInstance.getAllConnections().forEach(connection => {
        bindContextMenuToConnection(connection);
    });
    
    // Node context menu (keep as is)
    document.addEventListener('contextmenu', (e) => {
        let targetElement = e.target;
        let workflowNode = null;
        
        while (targetElement != null) {
            if (targetElement.classList && targetElement.classList.contains('workflow-node')) {
                workflowNode = targetElement;
                break;
            }
            targetElement = targetElement.parentElement;
        }
        
        if (workflowNode) {
            e.preventDefault();
            e.stopPropagation();
            selectedNode = workflowNode;
            selectedConnection = null;
            
            nodeMenu.style.display = 'block';
            nodeMenu.style.left = `${e.pageX}px`;
            nodeMenu.style.top = `${e.pageY}px`;
        }
    });
}


function setArrowType(type, connection = null) {
    const conn = connection || selectedConnection;
    if (conn) {
        try {
            // Update connection appearance based on type
            switch(type) {
                case 'pass':
                    conn.setPaintStyle({ stroke: '#28a745', strokeWidth: 2 });
                    break;
                case 'fail':
                    conn.setPaintStyle({ stroke: '#dc3545', strokeWidth: 2 });
                    break;
                case 'complete':
                    conn.setPaintStyle({ stroke: '#007bff', strokeWidth: 2 });
                    break;
                default:
                    conn.setPaintStyle({ stroke: '#28a745', strokeWidth: 2 });
            }
            
            // Get the existing data
            const existingData = conn.getData() || {};
            
            // Preserve anchor information
            const sourceAnchor = existingData.sourceAnchor || 
                                (conn.endpoints[0].anchor.type || "Right");
            const targetAnchor = existingData.targetAnchor || 
                                (conn.endpoints[1].anchor.type || "Left");
            
            // Store the type and anchor information
            conn.setData({ 
                type: type,
                sourceAnchor: sourceAnchor,
                targetAnchor: targetAnchor
            });

            // Hide the context menu after setting type
            const arrowMenu = document.getElementById('arrow-context-menu');
            if (arrowMenu) {
                arrowMenu.style.display = 'none';
            }
        } catch (error) {
            console.log('Error updating connection style:', error);
        }
    }
}

function deleteArrow() {
    if (selectedConnection) {
        jsPlumbInstance.deleteConnection(selectedConnection);
        selectedConnection = null;
        
        // Hide the context menu
        const arrowMenu = document.getElementById('arrow-context-menu');
        if (arrowMenu) {
            arrowMenu.style.display = 'none';
        }
    }
}

function editNode() {
    if (selectedNode) {
        const contentElement = selectedNode.querySelector('.node-content');
        if (contentElement) {
            const newLabel = prompt('Enter new label:', contentElement.innerHTML);
            if (newLabel) {
                contentElement.innerHTML = newLabel;
            }
        }
    }
}

function saveWorkflow() {
    return saveWorkflowBeforeExecution();
}


function loadWorkflowFile(input) {
    const file = input.files[0];
    if (!file) return;
    
    const reader = new FileReader();
    reader.onload = function(e) {
        try {
            const workflow = JSON.parse(e.target.result);
            loadWorkflow(workflow);
        } catch (error) {
            showToast(`Error loading workflow file: ${error}`, 'error');
        }
    };
    reader.readAsText(file);
}

function setWorkflowVariablesFromJson(workflowJson) {
    try {
        // Parse the JSON if it's a string
        let workflowData = workflowJson;
        if (typeof workflowJson === 'string') {
            workflowData = JSON.parse(workflowJson);
        }
        
        // Check if the workflow data contains variables
        if (!workflowData || !workflowData.variables) {
            console.warn('No variables found in workflow data');
            return false;
        }
        
        console.log('Loading workflow variables:', workflowData.variables);
        
        // Clear existing variable definitions
        workflowVariableDefinitions = {};
        
        // Set the variables from the workflow data
        Object.entries(workflowData.variables).forEach(([name, varData]) => {
            workflowVariableDefinitions[name] = {
                type: varData.type || 'string',
                defaultValue: varData.defaultValue || '',
                description: varData.description || ''
            };
            
            // Initialize the runtime variable value with the default value
            let value = varData.defaultValue;
            
            // Convert the value based on the type
            if (varData.type === 'number') {
                value = value !== '' ? Number(value) : 0;
            } else if (varData.type === 'boolean') {
                value = value === 'true' || value === true;
            } else if (varData.type === 'json') {
                try {
                    value = value !== '' ? JSON.parse(value) : {};
                } catch (e) {
                    console.error(`Error parsing JSON for variable ${name}:`, e);
                    value = {};
                }
            }
            
            // Set the initial runtime value
            workflowVariables[name] = value;
        });
        
        // Save to localStorage for persistence
        saveWorkflowVariableDefinitions();
        
        // Update any UI elements that display variables
        if (typeof updateVariablesTable === 'function') {
            updateVariablesTable();
        }
        
        console.log('Workflow variables loaded successfully');
        return true;
    } catch (error) {
        console.error('Error setting workflow variables from JSON:', error);
        return false;
    }
}

function loadWorkflow(workflow) {
    try {
        // Clear existing workflow
        const canvas = document.getElementById('workflow-canvas');
        if (canvas) {
            canvas.innerHTML = '';
            jsPlumbInstance.reset();  // This clears ALL event bindings!
        }
        startNode = null;
        nodeConfigs.clear();

        clearDebugPanelData();

        // Reset AI Builder session when loading a different workflow
        if (window.workflowBuilder) {
            const messagesContainer = document.getElementById('builderMessages');
            messagesContainer.innerHTML = '';
            window.workflowBuilder.resetSession();
            console.log('AI Builder session reset for loaded workflow');
        }

        // Create all nodes first
        workflow.nodes.forEach(node => {
            // Create node element
            const element = document.createElement('div');
            element.className = 'workflow-node';
            element.id = node.id;
            element.setAttribute('data-type', node.type);
            
            // Add content
            const contentContainer = document.createElement('div');
            contentContainer.className = 'node-content';
            
            // Add icon
            const icon = document.createElement('i');
            switch(node.type) {
                case 'Database':
                    icon.className = 'bi bi-database';
                    break;
                case 'File':
                    icon.className = 'bi bi-file-earmark';
                    break;
                case 'Server':
                    icon.className = 'bi bi-server';
                    break;
                case 'Alert':
                    icon.className = 'bi bi-exclamation-triangle';
                    break;
                case 'Document':
                    icon.className = 'bi bi-file-text';
                    break;
                case 'Folder Selector':
                    icon.className = 'bi bi-folder2';
                    break;
                case 'Set Variable':
                    icon.className = 'bi bi-braces';
                    break;
                case 'AI Action':
                    icon.className = 'bi bi-robot';
                    break;
                case 'Human Approval':
                    icon.className = 'bi bi-person-check';
                    break;
                case 'Conditional':
                    icon.className = 'bi bi-diagram-3';
                    break;
                case 'Loop':
                    icon.className = 'bi bi-arrow-repeat';
                    break;
                case 'End Loop':
                    icon.className = 'bi bi-arrow-return-left';
                    break;
                case 'AI Extract':
                    icon.className = 'bi bi-search';
                    break;
                case 'Excel Export':
                    icon.className = 'bi bi-file-earmark-excel';
                    break;
                default:
                    icon.className = 'bi bi-box';
            }
            
            contentContainer.appendChild(icon);
            //contentContainer.appendChild(document.createTextNode(' ' + node.label));
            contentContainer.appendChild(document.createTextNode(' ' + (node.type === 'Loop' ? 'Start Loop' : node.label)));
            element.appendChild(contentContainer);
            
            // Add endpoints
            const leftEndpoint = document.createElement('div');
            leftEndpoint.className = 'endpoint left-endpoint';
            element.appendChild(leftEndpoint);

            const rightEndpoint = document.createElement('div');
            rightEndpoint.className = 'endpoint right-endpoint';
            element.appendChild(rightEndpoint);
            
            // Set position
            element.style.left = node.position.left;
            element.style.top = node.position.top;
            
            // Set start node if applicable
            if (node.isStart) {
                startNode = element;
                element.classList.add('start-node');
                const startLabel = document.createElement('div');
                startLabel.className = 'start-label';
                startLabel.textContent = 'START';
                element.appendChild(startLabel);
            }

            canvas.appendChild(element);
            
            // Make node draggable
            jsPlumbInstance.draggable(element, {
                grid: [10, 10],
                drag: function(params) {
                    element.style.left = params.pos[0] + 'px';
                    element.style.top = params.pos[1] + 'px';
                    element.style.transform = 'none';
                    jsPlumbInstance.revalidate(element);
                },
                start: function(params) {
                    element.style.transform = 'none';
                },
                stop: function(params) {
                    element.style.left = params.pos[0] + 'px';
                    element.style.top = params.pos[1] + 'px';
                    element.style.transform = 'none';
                    jsPlumbInstance.repaintEverything();
                }
            });
            
            // Store node configuration
            if (node.config) {
                nodeConfigs.set(node.id, JSON.parse(JSON.stringify(node.config)));
            }
        });
        
        // Add jsPlumb endpoints to ALL nodes
        document.querySelectorAll('.workflow-node').forEach(node => {
            jsPlumbInstance.addEndpoint(node, {
                anchor: "Right",
                isSource: true,
                isTarget: true,
                maxConnections: -1,
                connectionType: "basic",
                endpoint: "Dot",
                endpointStyle: { fill: "#456" }
            });

            jsPlumbInstance.addEndpoint(node, {
                anchor: "Left",
                isSource: true,
                isTarget: true,
                maxConnections: -1,
                connectionType: "basic",
                endpoint: "Dot",
                endpointStyle: { fill: "#456" }
            });
        });
        
        // Create connections
        workflow.connections.forEach(conn => {
            const connection = jsPlumbInstance.connect({
                source: conn.source,
                target: conn.target,
                anchors: [
                    conn.sourceAnchor || "Right", 
                    conn.targetAnchor || "Left"
                ]
            });
            
            if (connection) {
                connection.setData({
                    type: conn.type || 'pass',
                    sourceAnchor: conn.sourceAnchor || "Right",
                    targetAnchor: conn.targetAnchor || "Left"
                });
                
                setArrowType(conn.type || 'pass', connection);
            }
        });
        
        // Reset counter to prevent ID conflicts
        const maxId = Math.max(...workflow.nodes.map(node => 
            parseInt(node.id.replace('node-', '')) || 0
        ));
        nodeCounter = maxId + 1;
        
        // CRITICAL FIX: Re-establish the connection event binding
        // This is lost when jsPlumbInstance.reset() is called above
        reestablishConnectionEventBinding();
        
        console.log('Workflow loaded successfully - connection event binding re-established');

    } catch (error) {
        console.error('Error loading workflow:', error);
        showToast(`Error loading workflow: ${error.message}`, 'error');
    }
}

function reestablishConnectionEventBinding() {
    // First, unbind any existing connection event to avoid duplicates
    jsPlumbInstance.unbind('connection');
    
    // Helper function to bind context menu to a connection
    function bindContextMenuToConnection(connection) {
        if (connection && connection.canvas) {
            connection.canvas.addEventListener('contextmenu', function(e) {
                e.preventDefault();
                e.stopPropagation();
                
                selectedConnection = connection;
                selectedNode = null;
                
                const arrowMenu = document.getElementById('arrow-context-menu');
                if (arrowMenu) {
                    arrowMenu.style.display = 'block';
                    arrowMenu.style.left = e.pageX + 'px';
                    arrowMenu.style.top = e.pageY + 'px';
                }
            });
        }
    }
    
    // Re-bind the connection event for NEW connections
    jsPlumbInstance.bind('connection', function(info) {
        console.log('New connection created after workflow load - binding context menu');
        
        // Set default connection type to pass
        setArrowType('pass', info.connection);
        
        // Store anchor information
        const sourceAnchor = info.connection.endpoints[0].anchor.type || "Right";
        const targetAnchor = info.connection.endpoints[1].anchor.type || "Left";
        info.connection.setData({
            type: 'pass',
            sourceAnchor: sourceAnchor,
            targetAnchor: targetAnchor
        });
        
        // Bind context menu with a small delay to ensure canvas is ready
        setTimeout(() => {
            bindContextMenuToConnection(info.connection);
        }, 50);
    });
    
    // Also bind context menus to all existing connections
    jsPlumbInstance.getAllConnections().forEach(connection => {
        bindContextMenuToConnection(connection);
    });
}


function setAsStart() {
    if (selectedNode) {
        // Remove start designation from previous start node
        if (startNode) {
            startNode.classList.remove('start-node');
            const oldLabel = startNode.querySelector('.start-label');
            if (oldLabel) {
                oldLabel.remove();
            }
        }

        // Set new start node
        startNode = selectedNode;
        startNode.classList.add('start-node');
        
        // Add start label if it doesn't exist
        if (!startNode.querySelector('.start-label')) {
            const startLabel = document.createElement('div');
            startLabel.className = 'start-label';
            startLabel.textContent = 'START';
            startNode.appendChild(startLabel);
        }

        // Hide the context menu
        const nodeMenu = document.getElementById('node-context-menu');
        if (nodeMenu) {
            nodeMenu.style.display = 'none';
        }
    }
}

function deleteNode() {
    if (selectedNode) {
        // Remove all connections to/from this node
        jsPlumbInstance.removeAllEndpoints(selectedNode);
        
        // If this was the start node, clear the reference
        if (selectedNode === startNode) {
            startNode = null;
        }
        
        // Remove the node from the DOM
        selectedNode.remove();
        selectedNode = null;
        
        // Hide the context menu
        const nodeMenu = document.getElementById('node-context-menu');
        if (nodeMenu) {
            nodeMenu.style.display = 'none';
        }
    }
}

function duplicateNode() {
    if (!selectedNode) return;
    
    const type = selectedNode.getAttribute('data-type');
    const x = parseInt(selectedNode.style.left) + 50;
    const y = parseInt(selectedNode.style.top) + 50;
    
    // Create the new node
    createNode(type, x, y);
    
    // Copy config to the new node
    const newNodeId = `node-${nodeCounter - 1}`;
    const config = nodeConfigs.get(selectedNode.id);
    if (config) {
        nodeConfigs.set(newNodeId, JSON.parse(JSON.stringify(config)));
    }
    
    // Hide the context menu
    const nodeMenu = document.getElementById('node-context-menu');
    if (nodeMenu) {
        nodeMenu.style.display = 'none';
    }
}

async function startWorkflow() {
    //if (isDebugMode) startWorkflow_Debug(); else startWorkflow_Engine();
    if (isDebugMode) startWorkflow_Engine(); else startWorkflow_Engine();
}

// Update the startWorkflow function to use the backend execution engine
async function startWorkflow_Engine() {
    addDebugLogEntry('Workflow execution started', 'info');

    if (!startNode) {
        showToast(`Please set a start node for the workflow`, 'warning');
        addDebugLogEntry('Please set a start node for the workflow', 'warning');
        return;
    }
    
    console.log(`Starting workflow...`);
    
    // Save the workflow first to ensure it's stored on the server
    const workflowId = await saveWorkflowBeforeExecution();
    if (!workflowId) {
        showToast('Failed to save workflow before execution', 'error');
        addDebugLogEntry('Failed to save workflow before execution', 'error');
        return;
    }
    
    try {
        // Call the API to start the workflow execution
        const response = await fetch('/api/workflow/run', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                workflow_id: workflowId,
                initiator: 'workflow_designer' // You could replace this with the actual user
            })
        });
        
        const result = await response.json();
        
        if (result.status === 'success') {
            const executionId = result.execution_id;
            
            // Update UI to show workflow is running
            const runBtn = document.getElementById('runWorkflowBtn');
            const stopBtn = document.getElementById('stopWorkflowBtn');
            const statusDiv = document.getElementById('workflowStatus');

            runBtn.classList.remove('active');
            stopBtn.classList.add('active');

            runBtn.style.display = 'none';
            stopBtn.style.display = 'inline-block';
            statusDiv.style.display = 'inline-block';
            
            isWorkflowRunning = true;
            
            // Store the execution ID for stopping/pausing
            currentExecutionId = executionId;
            
            // Show a toast with a link to the monitoring dashboard
            showToast(`
                Workflow execution started! 
                <a href="/monitoring" target="_blank" class="text-white text-decoration-underline">
                    Open monitoring dashboard
                </a>
            `, 'success', 5000);
            
            // Start polling for execution status updates
            startStatusPolling(executionId);
        } else {
            showToast(`Error starting workflow: ${result.message}`, 'error');
            addDebugLogEntry(`Error starting workflow: ${result.message}`, 'error');
        }
    } catch (error) {
        console.error('Error starting workflow:', error);
        showToast(`Error starting workflow: ${error.message}`, 'error');
        addDebugLogEntry(`Error starting workflow: ${error.message}`, 'error');
    }
}

// Update the stopWorkflow function
function stopWorkflow(justUI = false) {
    if (!justUI && currentExecutionId) {
        // Call the API to cancel the execution
        fetch(`/api/workflow/executions/${currentExecutionId}/cancel`, {
            method: 'POST'
        })
        .then(response => response.json())
        .then(result => {
            if (result.status === 'success') {
                showToast('Workflow execution cancelled', 'info');
            } else {
                showToast(`Failed to cancel workflow: ${result.message}`, 'warning');
            }
        })
        .catch(error => {
            console.error('Error cancelling workflow:', error);
        });
        
        stopStatusPolling();
    }
    
    // Reset UI state
    isWorkflowRunning = false;
    currentExecutionId = null;
    
    const runBtn = document.getElementById('runWorkflowBtn');
    const stopBtn = document.getElementById('stopWorkflowBtn');
    const statusDiv = document.getElementById('workflowStatus');
    
    stopBtn.classList.remove('active');
    runBtn.classList.add('active');
    
    runBtn.style.display = 'inline-block';
    stopBtn.style.display = 'none';
    if (!justUI) {
        statusDiv.style.display = 'none';
    }
    
    // Reset all nodes' visual states if not in monitoring mode
    if (!justUI) {
        document.querySelectorAll('.workflow-node').forEach(node => {
            node.classList.remove('executing', 'completed', 'error', 'paused');
        });
    }
}

function showStatus(message, type) {
    console.log(`Showing Status: ${message}`);
    const statusDiv = document.getElementById('workflowStatus');
    statusDiv.textContent = message;
    statusDiv.className = 'workflow-status';
    statusDiv.classList.add(`bg-${type}`);
    statusDiv.style.display = 'inline-block';
}

async function executeWorkflowNode(node, prev_data = {}, visitedNodes = new Set()) {
    if (!isWorkflowRunning) {
        throw new Error('Workflow execution stopped');
    }

    const nodeId = node.id;
    const nodeName = node.querySelector('.node-content').textContent.trim();
    const nodeType = node.getAttribute('data-type');
    
    // Check if we've already visited this node in the current execution path
    if (visitedNodes.has(nodeId)) {
        if (isDebugMode) {
            addDebugLogEntry(`Skipping already visited node: ${nodeName}`, 'debug');
        }
        return;
    }
    visitedNodes.add(nodeId);
    
    // Mark node as executing
    node.classList.add('executing');
    node.classList.remove('completed', 'error');
    
    // Debug mode handling
    if (isDebugMode) {
        node.classList.add('debug-current');
        addDebugLogEntry(`Executing node: ${nodeName} (${nodeType})`, 'info');
        const config = nodeConfigs.get(nodeId) || {};
        addDebugLogEntry(`Node configuration (before execution): ${formatJsonOutput(config)}`, 'info');
        if (Object.keys(prev_data).length > 0) {
            addDebugLogEntry(`Input data: ${formatJsonOutput(prev_data)}`, 'info');
        }
        updateVariablesTable();
    }

    try {
        // Execute the node
        const result = await executeNodeAction(node, prev_data);

        // Debug mode handling
        if (isDebugMode) {
            const config = nodeConfigs.get(nodeId) || {};
            addDebugLogEntry(`Node configuration (after execution): ${formatJsonOutput(config)}`, 'info');
        }
        
        // Store previous data for the next node
        if (result.success) {
            prev_data = result.data;
        }
        
        // Mark node as completed
        node.classList.remove('executing');
        node.classList.add('completed');
        
        if (isDebugMode) {
            node.classList.remove('debug-current');
            
            if (result.success) {
                addDebugLogEntry(`Node completed successfully: ${nodeName}`, 'success');
                if (result.data && Object.keys(result.data).length > 0) {
                    addDebugLogEntry(`Output data: ${formatJsonOutput(result.data)}`, 'info');
                }
            } else {
                addDebugLogEntry(`Node failed: ${nodeName}`, 'error');
                if (result.error) {
                    addDebugLogEntry(`Error: ${result.error}`, 'error');
                }
            }
            
            // Store node output
            nodeOutputs[nodeId] = {
                name: nodeName,
                data: result.data || {},
                success: result.success,
                error: result.error
            };
            
            // Update execution path
            executionPath.push({
                nodeId: nodeId,
                nodeName: nodeName,
                success: result.success
            });
            
            updateNodeOutputSelect();
            updateExecutionPath();
        }
        
        // Handle special case for Loop nodes
        if (nodeType === 'Loop') {
            // Check if loop has completed and should continue from End Loop
            const continueFromEndLoop = node.getAttribute('data-continue-from-end-loop');
            if (continueFromEndLoop) {
                node.removeAttribute('data-continue-from-end-loop');
                const endLoopNode = document.getElementById(continueFromEndLoop);
                
                if (endLoopNode) {
                    if (isDebugMode) {
                        addDebugLogEntry(`Loop completed, continuing from End Loop node`, 'info');
                    }
                    
                    // Execute the End Loop node
                    await executeWorkflowNode(endLoopNode, prev_data, visitedNodes);
                    return;  // Don't process other connections from the Loop node
                }
            }
            
            // Check if this loop was just completed
            if (window.completedLoops && window.completedLoops.has(nodeId)) {
                // Skip the loop body connection and find End Loop to continue
                const endLoopNode = findEndLoopNode(nodeId);
                if (endLoopNode) {
                    if (isDebugMode) {
                        addDebugLogEntry(`Loop already completed, continuing from End Loop`, 'info');
                    }
                    await executeWorkflowNode(endLoopNode, prev_data, visitedNodes);
                    return;
                }
            }
        }
        
        // Handle special case for End Loop nodes
        if (nodeType === 'End Loop') {
            // Check if we're still in a loop (shouldn't happen with proper flow)
            const config = nodeConfigs.get(nodeId) || {};
            let loopNodeId = config.loopNodeId;
            
            if (!loopNodeId && window.activeLoops && window.activeLoops.size > 0) {
                const activeLoopIds = Array.from(window.activeLoops.keys());
                loopNodeId = activeLoopIds[activeLoopIds.length - 1];
            }
            
            // If we're not in an active loop, continue with connections from End Loop
            if (!window.activeLoops || !window.activeLoops.has(loopNodeId)) {
                // Clean up completed loops tracking for this loop
                if (window.completedLoops && loopNodeId) {
                    window.completedLoops.delete(loopNodeId);
                }
                
                // Continue with connections from End Loop node
                const connections = jsPlumbInstance.getConnections({ source: nodeId });
                for (const connection of connections) {
                    const targetNode = document.getElementById(connection.targetId);
                    const connectionType = connection.getData().type || 'pass';
                    
                    if (targetNode && shouldFollowPath(connectionType, result)) {
                        if (isDebugMode) {
                            addDebugLogEntry(`Following ${connectionType} path from End Loop to: ${targetNode.querySelector('.node-content').textContent.trim()}`, 'info');
                        }
                        await executeWorkflowNode(targetNode, prev_data, visitedNodes);
                    }
                }
                return;  // Exit after processing End Loop connections
            }
        }
        
        // Get all connections from this node
        const connections = jsPlumbInstance.getConnections({ source: nodeId });
        
        // Process connections normally for non-Loop nodes
        for (const connection of connections) {
            const targetNode = document.getElementById(connection.targetId);
            const connectionType = connection.getData().type || 'pass';
            
            // Skip loop body connection if loop is completed
            if (nodeType === 'Loop' && window.completedLoops && window.completedLoops.has(nodeId)) {
                if (connectionType === 'pass') {
                    if (isDebugMode) {
                        addDebugLogEntry(`Skipping loop body connection (loop completed)`, 'debug');
                    }
                    continue;  // Skip this connection
                }
            }
            
            if (shouldFollowPath(connectionType, result)) {
                if (isDebugMode) {
                    addDebugLogEntry(`Following ${connectionType} path to node: ${targetNode.querySelector('.node-content').textContent.trim()}`, 'info');
                }
                await executeWorkflowNode(targetNode, prev_data, visitedNodes);
            } else {
                if (isDebugMode) {
                    addDebugLogEntry(`Skipping ${connectionType} path to node: ${targetNode.querySelector('.node-content').textContent.trim()} - condition not met`, 'info');
                }
            }
        }

        // Update UI if needed
        if (isDebugMode) {
            updateVariablesTable();
        }
        
    } catch (error) {
        // Error handling
        node.classList.remove('executing');
        node.classList.add('error');
        
        if (isDebugMode) {
            node.classList.remove('debug-current');
            addDebugLogEntry(`Error in node ${nodeName}: ${error.message}`, 'error');
            
            nodeOutputs[nodeId] = {
                name: nodeName,
                data: {},
                success: false,
                error: error.message
            };
            
            updateNodeOutputSelect();
            updateExecutionPath();
        }
        
        throw error;
    }
}

// Clean up completed loops when workflow stops
const originalStopWorkflow = window.stopWorkflow || function() {};
window.stopWorkflow = function() {
    // Clean up loop tracking
    if (window.completedLoops) {
        window.completedLoops.clear();
    }
    if (window.activeLoops) {
        window.activeLoops.clear();
    }
    if (window.loopResults) {
        window.loopResults.clear();
    }
    
    // Remove any loop-related attributes
    document.querySelectorAll('[data-iteration]').forEach(node => {
        node.removeAttribute('data-iteration');
    });
    document.querySelectorAll('[data-continue-from-end-loop]').forEach(node => {
        node.removeAttribute('data-continue-from-end-loop');
    });
    
    // Call original stop function
    return originalStopWorkflow.apply(this, arguments);
};

function shouldFollowPath(connectionType, result) {
    switch (connectionType) {
        case 'pass':
            return result.success;
        case 'fail':
            return !result.success;
        case 'complete':
            return true;
        default:
            return result.success;
    }
}

async function executeNodeAction(node, prev_data = {}) {
    const type = node.getAttribute('data-type');
    const config = nodeConfigs.get(node.id) || {};

    // Debug mode handling (keep as is)
    if (isDebugMode && (type === 'AI Action' || type === 'Folder Selector' || type === 'AI Extract')) {
        addDebugLogEntry(`************************************************************************`, 'info');
        addDebugLogEntry(`Executing node: ${type}`, 'info');
        addDebugLogEntry(`Node configuration: ${formatJsonOutput(config)}`, 'info');
        addDebugLogEntry(`************************************************************************`, 'info');
    }

    // Execute based on node type
    try {
        switch (type) {
            case 'Database':
                return await executeDatabaseAction(config, prev_data);
            case 'File':
                return await executeFileAction(config, node);
            case 'Server':
                return await executeServerAction(config);
            case 'Alert':
                return await executeAlertAction(config);
            case 'Document':
                return await executeDocumentAction(config);
            case 'Folder Selector':  // Add this case
                return await executeFolderSelectorAction(config, node, prev_data);
            case 'AI Action':
                return await executeAIAction(config, node, prev_data);
            case 'Conditional':
                return await executeConditionalAction(config, prev_data);
            case 'Loop':
                return await executeLoopNode(config, node, prev_data);
            case 'End Loop':
                return await executeEndLoopNode(config, node, prev_data);
            case 'Human Approval':
                console.log('=====>>>>> executeHumanApproval!');
                return await executeHumanApproval(config, prev_data);
            default:
                throw new Error(`Unknown node type: ${type}`);
        }
    } catch (error) {
        console.error(`Error executing ${type} node:`, error);
        return { success: false, error: error.message, data: {} };
    }
}

// Enhanced database operation execution
async function executeDatabaseAction(config, prev_data = {}) {
    console.log('Executing database action with config:', config);
    
    if (!config.connection) {
        return { 
            success: false, 
            error: 'Database connection is required',
            data: {}
        };
    }
    
    try {
        let result;
        let query = '';
        let parameters = [];
        
        // Process configuration based on operation type
        switch (config.dbOperation) {
            case 'query':
                // Replace variables in query
                query = replaceVariableReferences(config.query, workflowVariables);
                
                // Log the processed query in debug mode
                if (isDebugMode) {
                    addDebugLogEntry(`Executing SQL query: ${query}`, 'info');
                }
                
                // Execute query
                result = await executeDatabaseQuery(config.connection, query);
                break;
                
            case 'procedure':
                // Get procedure name with variable replacements
                const procedure = replaceVariableReferences(config.procedure, workflowVariables);
                
                // Process parameters with variable replacements
                let paramJson = config.parameters;
                try {
                    // Replace variable references in the entire JSON string first
                    paramJson = replaceVariableReferences(paramJson, workflowVariables);
                    
                    // Parse the JSON string to get parameters
                    parameters = JSON.parse(paramJson);
                    
                    if (isDebugMode) {
                        addDebugLogEntry(`Executing stored procedure: ${procedure} with parameters: ${JSON.stringify(parameters)}`, 'info');
                    }
                    
                    // Execute stored procedure
                    result = await executeStoredProcedure(config.connection, procedure, parameters);
                } catch (e) {
                    throw new Error(`Error parsing procedure parameters: ${e.message}`);
                }
                break;
                
            case 'select':
                // Build SELECT query from components
                const columns = replaceVariableReferences(config.columns, workflowVariables) || '*';
                const table = replaceVariableReferences(config.tableName, workflowVariables);
                const whereClause = replaceVariableReferences(config.whereClause, workflowVariables);
                
                if (!table) {
                    throw new Error('Table name is required for SELECT operation');
                }
                
                // Construct the query
                query = `SELECT ${columns} FROM ${table}`;
                if (whereClause) {
                    query += ` WHERE ${whereClause}`;
                }
                
                if (isDebugMode) {
                    addDebugLogEntry(`Executing SELECT query: ${query}`, 'info');
                }
                
                // Execute query
                result = await executeDatabaseQuery(config.connection, query);
                break;
                
            case 'insert':
                // Get table name with variable replacements
                const insertTable = replaceVariableReferences(config.tableName, workflowVariables);
                if (!insertTable) {
                    throw new Error('Table name is required for INSERT operation');
                }
                
                // Get data to insert
                let insertData;
                if (config.dataSource === 'direct') {
                    try {
                        // Replace variables in the JSON string
                        const dataStr = replaceVariableReferences(config.data, workflowVariables);
                        insertData = JSON.parse(dataStr);
                    } catch (e) {
                        throw new Error(`Error parsing INSERT data: ${e.message}`);
                    }
                } else if (config.dataSource === 'variable') {
                    const varName = config.dataVariable;
                    if (!workflowVariables.hasOwnProperty(varName)) {
                        throw new Error(`Variable "${varName}" not found for INSERT operation`);
                    }
                    insertData = workflowVariables[varName];
                } else if (config.dataSource === 'previous') {
                    // Extract data from previous step output using path
                    const path = config.dataPath;
                    insertData = getNestedValue(prev_data, path);
                    
                    if (insertData === undefined) {
                        throw new Error(`Path "${path}" not found in previous step output`);
                    }
                }
                
                // Validate data
                if (!insertData || typeof insertData !== 'object') {
                    throw new Error('Invalid data for INSERT operation');
                }
                
                // Build the INSERT query
                const columnsI = Object.keys(insertData);
                const values = Object.values(insertData);
                
                if (columnsI.length === 0) {
                    throw new Error('No data columns provided for INSERT operation');
                }
                
                // Format values for SQL
                const sqlValues = values.map(val => {
                    if (val === null) return 'NULL';
                    if (typeof val === 'string') return `'${val.replace(/'/g, "''")}'`;
                    if (typeof val === 'object') return `'${JSON.stringify(val).replace(/'/g, "''")}'`;
                    return val;
                });
                
                query = `INSERT INTO ${insertTable} (${columnsI.join(', ')}) VALUES (${sqlValues.join(', ')})`;
                
                if (isDebugMode) {
                    addDebugLogEntry(`Executing INSERT query: ${query}`, 'info');
                }
                
                // Execute query
                result = await executeDatabaseQuery(config.connection, query);
                break;
                
            case 'update':
                // Get table name with variable replacements
                const updateTable = replaceVariableReferences(config.tableName, workflowVariables);
                const updateWhere = replaceVariableReferences(config.whereClause, workflowVariables);
                
                if (!updateTable) {
                    throw new Error('Table name is required for UPDATE operation');
                }
                
                if (!updateWhere) {
                    throw new Error('WHERE clause is required for UPDATE operation');
                }
                
                // Get data to update
                let updateData;
                if (config.dataSource === 'direct') {
                    try {
                        // Replace variables in the JSON string
                        const dataStr = replaceVariableReferences(config.data, workflowVariables);
                        updateData = JSON.parse(dataStr);
                    } catch (e) {
                        throw new Error(`Error parsing UPDATE data: ${e.message}`);
                    }
                } else if (config.dataSource === 'variable') {
                    const varName = config.dataVariable;
                    if (!workflowVariables.hasOwnProperty(varName)) {
                        throw new Error(`Variable "${varName}" not found for UPDATE operation`);
                    }
                    updateData = workflowVariables[varName];
                } else if (config.dataSource === 'previous') {
                    // Extract data from previous step output using path
                    const path = config.dataPath;
                    updateData = getNestedValue(prev_data, path);
                    
                    if (updateData === undefined) {
                        throw new Error(`Path "${path}" not found in previous step output`);
                    }
                }
                
                // Validate data
                if (!updateData || typeof updateData !== 'object') {
                    throw new Error('Invalid data for UPDATE operation');
                }
                
                // Build the SET clause
                const setClauses = [];
                for (const [key, value] of Object.entries(updateData)) {
                    let sqlValue;
                    if (value === null) {
                        sqlValue = 'NULL';
                    } else if (typeof value === 'string') {
                        sqlValue = `'${value.replace(/'/g, "''")}'`;
                    } else if (typeof value === 'object') {
                        sqlValue = `'${JSON.stringify(value).replace(/'/g, "''")}'`;
                    } else {
                        sqlValue = value;
                    }
                    setClauses.push(`${key} = ${sqlValue}`);
                }
                
                if (setClauses.length === 0) {
                    throw new Error('No data columns provided for UPDATE operation');
                }
                
                query = `UPDATE ${updateTable} SET ${setClauses.join(', ')} WHERE ${updateWhere}`;
                
                if (isDebugMode) {
                    addDebugLogEntry(`Executing UPDATE query: ${query}`, 'info');
                }
                
                // Execute query
                result = await executeDatabaseQuery(config.connection, query);
                break;
                
            case 'delete':
                // Get table name with variable replacements
                const deleteTable = replaceVariableReferences(config.tableName, workflowVariables);
                const deleteWhere = replaceVariableReferences(config.whereClause, workflowVariables);
                
                if (!deleteTable) {
                    throw new Error('Table name is required for DELETE operation');
                }
                
                if (!deleteWhere) {
                    throw new Error('WHERE clause is required for DELETE operation');
                }
                
                query = `DELETE FROM ${deleteTable} WHERE ${deleteWhere}`;
                
                if (isDebugMode) {
                    addDebugLogEntry(`Executing DELETE query: ${query}`, 'info');
                }
                
                // Execute query
                result = await executeDatabaseQuery(config.connection, query);
                break;
                
            default:
                throw new Error(`Unknown database operation: ${config.dbOperation}`);
        }
        
        // Process result
        if (result && result.status === 'success') {
            // Store result in variable if configured
            if (config.saveToVariable && config.outputVariable) {
                workflowVariables[config.outputVariable] = result.response || result.data || {};
                
                if (isDebugMode) {
                    if (typeof result.response === 'object' && result.response !== null) {
                        const rowCount = Array.isArray(result.response) ? result.response.length : 0;
                        addDebugLogEntry(`Stored database result in variable "${config.outputVariable}": ${rowCount} rows`, 'info');
                    } else {
                        addDebugLogEntry(`Stored database result in variable "${config.outputVariable}"`, 'info');
                    }
                }
            }
            
            return {
                success: true,
                data: {
                    operation: config.dbOperation,
                    result: result.response || result.data || {},
                    rowsAffected: result.affected_rows || 0
                }
            };
        } else {
            throw new Error(result?.error || 'Unknown database error');
        }
    } catch (error) {
        if (isDebugMode) {
            addDebugLogEntry(`Database operation error: ${error.message}`, 'error');
        }
        
        // Check if we should continue on error
        if (config.continueOnError) {
            if (isDebugMode) {
                addDebugLogEntry(`Continuing workflow despite database error`, 'warning');
            }
            
            // Set output variable to empty result to indicate failure
            if (config.saveToVariable && config.outputVariable) {
                workflowVariables[extractVariableName(config.outputVariable)] = { error: error.message };
                
                if (isDebugMode) {
                    addDebugLogEntry(`Set variable "${config.outputVariable}" to error result`, 'info');
                }
            }
            
            return {
                success: true, // Return success so workflow continues
                data: {
                    operation: config.dbOperation,
                    error: error.message,
                    success: false
                }
            };
        }
        
        return {
            success: false,
            error: error.message,
            data: {
                operation: config.dbOperation
            }
        };
    }
}

// Function to execute a stored procedure with parameters
async function executeStoredProcedure(connectionId, procedureName, parameters) {
    // Construct the API endpoint
    const endpoint = `/execute/procedure/${connectionId}/${procedureName}`;
    
    // Convert parameters to the format expected by the API
    const requestParams = parameters.map(param => {
        return {
            name: param.name,
            value: param.value,
            type: param.type || 'string'
        };
    });
    
    // Make the API call
    try {
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            },
            body: JSON.stringify(requestParams)
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error: ${response.status} ${response.statusText}`);
        }
        
        const result = await response.json();
        return result;
    } catch (error) {
        console.error('Error executing stored procedure:', error);
        throw error;
    }
}

// Implementation of file operations
async function executeFileAction(config, node, prev_data = {}) {
    console.log('Executing file action:', config);
    
    // Replace variable references in file path
    const filePath = replaceVariableReferences(config.filePath, workflowVariables);

    if (!filePath) {
        return { 
            success: false, 
            error: 'File path is required',
            data: { operation: config.operation, filePath: config.filePath }
        };
    }
    
    try {
        let result;
        let content;
        
        // Prepare content for write/append operations
        if (config.operation === 'write' || config.operation === 'append') {
            if (config.contentSource === 'direct') {
                content = replaceVariableReferences(config.content, workflowVariables);
            } else if (config.contentSource === 'variable') {
                const varName = config.contentVariable;
                if (workflowVariables.hasOwnProperty(varName)) {
                    content = workflowVariables[varName];
                    if (typeof content !== 'string') {
                        // Convert objects/arrays to JSON strings
                        content = JSON.stringify(content);
                    }
                } else {
                    return { 
                        success: false, 
                        error: `Variable "${varName}" not found`,
                        data: { operation: config.operation, filePath }
                    };
                }
            } else if (config.contentSource === 'previous') {
                // Extract data from previous step output using path
                const path = config.contentPath;
                content = getNestedValue(prev_data, path);
                
                if (content === undefined) {
                    return { 
                        success: false, 
                        error: `Path "${path}" not found in previous step output`,
                        data: { operation: config.operation, filePath }
                    };
                }
                
                if (typeof content !== 'string') {
                    // Convert objects/arrays to JSON strings
                    content = JSON.stringify(content);
                }
            }
        }

        // Prepare destination path for copy/move operations
        let destinationPath;
        if (config.operation === 'copy' || config.operation === 'move') {
            destinationPath = replaceVariableReferences(config.destinationPath, workflowVariables);
            
            if (!destinationPath) {
                return { 
                    success: false, 
                    error: 'Destination path is required for copy/move operations',
                    data: { operation: config.operation, filePath }
                };
            }
        }
        
        // Execute file operation
        switch (config.operation) {
            case 'read':
                // API call to read file
                const readResponse = await fetch(`/workflow/file/read`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filePath })
                });
                
                if (!readResponse.ok) {
                    if (isDebugMode) {
                        addDebugLogEntry(`Error reading file ${filePath}: ${readResponse.statusText}`, 'error');
                    }
                    throw new Error(`Failed to read file: ${readResponse.statusText}`);
                }
                
                result = await readResponse.json();
                console.log(`Saving to variable boolean: ${config.saveToVariable}`);
                console.log(`Saving to variable: ${config.outputVariable}`);
                // Store result in variable if configured
                if (config.saveToVariable && node._originalConfig.outputVariable) {
                    // TODO fix this
                    const fileOutputVariable = extractVariableName(node._originalConfig.outputVariable);
                    workflowVariables[fileOutputVariable] = result.content;
                    
                    if (isDebugMode) {
                        addDebugLogEntry(`Stored file content in variable "${fileOutputVariable}"`, 'info');
                    }
                }
                
                return {
                    success: true,
                    data: {
                        operation: 'read',
                        filePath,
                        content: result.content,
                        size: result.content.length
                    }
                };
                
            case 'write':
                // API call to write file
                const writeResponse = await fetch(`/workflow/file/write`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filePath, content, overwrite: true })
                });
                
                if (!writeResponse.ok) {
                    if (isDebugMode) {
                        addDebugLogEntry(`Error writing to file ${filePath}: ${writeResponse.statusText}`, 'error');
                    }
                    throw new Error(`Failed to write file: ${writeResponse.statusText}`);
                }
                
                result = await writeResponse.json();
                
                // Store result in variable if configured
                if (config.saveToVariable && config.outputVariable) {
                    workflowVariables[extractVariableName(config.outputVariable)] = true;
                    
                    if (isDebugMode) {
                        addDebugLogEntry(`Set variable "${config.outputVariable}" to true (write success)`, 'info');
                    }
                }
                
                return {
                    success: true,
                    data: {
                        operation: 'write',
                        filePath,
                        bytesWritten: content.length
                    }
                };
                
            case 'append':
                // API call to append to file
                const appendResponse = await fetch(`/workflow/file/append`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filePath, content })
                });
                
                if (!appendResponse.ok) {
                    if (isDebugMode) {
                        addDebugLogEntry(`Error appending to file ${filePath}: ${appendResponse.statusText}`, 'error');
                    }
                    throw new Error(`Failed to append to file: ${appendResponse.statusText}`);
                }
                
                result = await appendResponse.json();
                
                // Store result in variable if configured
                if (config.saveToVariable && config.outputVariable) {
                    workflowVariables[extractVariableName(config.outputVariable)] = true;
                    
                    if (isDebugMode) {
                        addDebugLogEntry(`Set variable "${config.outputVariable}" to true (append success)`, 'info');
                    }
                }
                
                return {
                    success: true,
                    data: {
                        operation: 'append',
                        filePath,
                        bytesWritten: content.length
                    }
                };
                
            case 'check':
                // API call to check if file exists
                const checkResponse = await fetch(`/workflow/file/check`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filePath })
                });
                
                if (!checkResponse.ok) {
                    if (isDebugMode) {
                        addDebugLogEntry(`Error checking file ${filePath}: ${checkResponse.statusText}`, 'error');
                    }
                    throw new Error(`Failed to check file: ${checkResponse.statusText}`);
                }
                
                result = await checkResponse.json();
                
                // Store result in variable if configured
                if (config.saveToVariable && node._originalConfig.outputVariable) {
                    const fileOutputVariable = extractVariableName(node._originalConfig.outputVariable);
                    workflowVariables[fileOutputVariable] = result.exists;
                    
                    if (isDebugMode) {
                        addDebugLogEntry(`Set variable "${fileOutputVariable}" to ${result.exists} (file exists)`, 'info');
                    }
                }
                
                return {
                    success: true,
                    data: {
                        operation: 'check',
                        filePath,
                        exists: result.exists
                    }
                };
                
            case 'delete':
                // API call to delete file
                const deleteResponse = await fetch(`/workflow/file/delete`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filePath })
                });
                
                if (!deleteResponse.ok) {
                    if (isDebugMode) {
                        addDebugLogEntry(`Error deleting file ${filePath}: ${deleteResponse.statusText}`, 'error');
                    }
                    throw new Error(`Failed to delete file: ${deleteResponse.statusText}`);
                }
                
                result = await deleteResponse.json();
                
                // Store result in variable if configured
                if (config.saveToVariable && node._originalConfig.outputVariable) {
                    const fileOutputVariable = extractVariableName(node._originalConfig.outputVariable);
                    workflowVariables[fileOutputVariable] = result.success;
                    
                    if (isDebugMode) {
                        addDebugLogEntry(`Set variable "${fileOutputVariable}" to ${result.success} (delete success)`, 'info');
                    }
                }
                
                return {
                    success: true,
                    data: {
                        operation: 'delete',
                        filePath,
                        deleted: result.success
                    }
                };
                
            default:
                throw new Error(`Unknown file operation: ${config.operation}`);
        }
    } catch (error) {
        // Check if we should continue on error
        if (config.continueOnError) {
            if (isDebugMode) {
                addDebugLogEntry(`File operation error, but continuing: ${error.message}`, 'warning');
            }
            
            // Set output variable to false to indicate failure
            if (config.saveToVariable && config.outputVariable) {
                workflowVariables[extractVariableName(config.outputVariable)] = false;
                
                if (isDebugMode) {
                    addDebugLogEntry(`Set variable "${config.outputVariable}" to false (operation failed)`, 'info');
                }
            }
            
            return {
                success: true, // Return success so workflow continues
                data: {
                    operation: config.operation,
                    filePath,
                    error: error.message,
                    success: false
                }
            };
        }
        
        return {
            success: false,
            error: error.message,
            data: {
                operation: config.operation,
                filePath
            }
        };
    }
}
async function executeServerAction(config, prev_output) {
    console.log('Executing server action:', config);
    try {
        const response = await fetch(config.url, {
            method: config.method,
            headers: JSON.parse(config.headers || '{}')
        });
        return { success: response.ok, data: await response.json() };
    } catch (error) {
        return { success: false, error: error.message };
    }
}

async function executeAlertAction(config, prev_output) {
    console.log('Executing alert action:', config);
    alert_type = config.alertType;
    email_to = config.recipients;
    subject = config.emailSubject || 'Workflow Notification';
    message = config.messageTemplate;
    if (config.attachmentPath) {
        console.warn('Attachment path specified but attachments are only supported during backend workflow execution, not in frontend preview.');
    }
    console.log(`Email To: ${email_to}, Subject: ${subject}`);
    result = await sendEmailNotification(email_to, subject, message);
    return { success: true, data: { sent: true } };
}

// Document node execution function
async function executeDocumentAction(config, prev_data = {}) {
    console.log('Executing document action:', config);
    
    if (isDebugMode) {
        addDebugLogEntry(`Executing document action: ${config.documentAction}`, 'info');
    }
    
    try {
        let result;
        
        // Handle different document actions
        switch (config.documentAction) {
            case 'process':
                result = await processDocument(config, prev_data);
                break;
            case 'extract':
                result = await extractDocument(config, prev_data);
                break;
            case 'analyze':
                result = await analyzeDocument(config, prev_data);
                break;
            case 'get':
                result = await getDocument(config, prev_data);
                break;
            case 'save':
                result = await saveDocument(config, prev_data);
                break;
            default:
                throw new Error(`Unknown document action: ${config.documentAction}`);
        }
        
        // Handle output based on output type
        if (result.success) {
            console.log(`Handling output type ${config.outputType}`);
            console.log(`Handling output path ${config.outputPath}`);
            
            if (config.outputType === 'variable' && config.outputPath) {
                // Get variable name with any variable references replaced
                const variableName = extractVariableName(config.outputPath);
                console.log(`variableName = ${variableName}`);
                // Store in workflow variable
                workflowVariables[variableName] = result.data;
                
                if (isDebugMode) {
                    addDebugLogEntry(`Set variable "${variableName}" to document result data`, 'info');
                    // Update variables table
                    updateVariablesTable();
                }
            } else if (config.outputType === 'file' && config.outputPath && config.documentAction !== 'save') {
                // Process the output path to replace any variable references
                const processedOutputPath = replaceVariableReferences(config.outputPath, workflowVariables);
                
                if (!processedOutputPath) {
                    console.error('Output path is empty after variable substitution');
                    
                    // Add error to result but don't fail the entire operation
                    result.fileSaved = false;
                    result.fileError = "Output path is empty after variable substitution";
                    
                    if (isDebugMode) {
                        addDebugLogEntry(`Cannot save file: Output path is empty after variable substitution`, 'error');
                    }
                } else {
                    // Save to file if not already saved by the action
                    try {
                        // Prepare content for saving
                        let contentToSave;
                        if (typeof result.data === 'string') {
                            contentToSave = result.data;
                        } else if (result.data.content) {
                            // If there's already a content field, use that
                            contentToSave = result.data.content;
                        } else {
                            // Otherwise stringify the entire data object
                            contentToSave = JSON.stringify(result.data, null, 2);
                        }
                        
                        console.log('Saving document output to file:', processedOutputPath);
                        console.log('Content preview:', contentToSave.substring(0, 100) + '...');
                        
                        const docSaveRoute = await getDocumentURL('/document/save');
                        const saveResponse = await fetch(docSaveRoute, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                content: contentToSave,
                                outputPath: processedOutputPath
                            })
                        });
                        
                        if (!saveResponse.ok) {
                            const errorData = await saveResponse.json();
                            throw new Error(errorData.message || 'Failed to save output to file');
                        }
                        
                        const saveResult = await saveResponse.json();
                        console.log('Save result:', saveResult);
                        
                        // Add save result to the original result
                        result.fileSaved = true;
                        result.filePath = processedOutputPath;
                        
                        if (isDebugMode) {
                            addDebugLogEntry(`Saved document output to file: ${processedOutputPath}`, 'success');
                        }
                    } catch (saveError) {
                        console.error('Error saving document output to file:', saveError);
                        
                        // Don't fail the whole operation if just the save fails
                        result.fileSaved = false;
                        result.fileError = saveError.message;
                        
                        if (isDebugMode) {
                            addDebugLogEntry(`Error saving to file: ${saveError.message}`, 'error');
                        }
                    }
                }
            } else if (config.outputType === 'return') {
                // Set the output type to the data return value
                console.log('Returning data as output to next node...');
                result = result.data;
            }
        }
        
        return result;
    } catch (error) {
        console.error('Error executing document action:', error);
        return { success: false, error: error.message, data: {} };
    }
}

// Process document function
async function processDocument(config, prev_data) {
    console.log('Getting document sources...');
    // Get document source
    const documentSource = await getDocumentSource(config, prev_data);
    if (!documentSource.success) {
        return documentSource; // Return the error
    }
    console.log(`Document source data: ${documentSource.data}`);
    console.log(`Document source type: ${documentSource.type}`);
    const formData = new FormData();
    
    if (documentSource.type === 'file') {
        // If it's a file object, add it to FormData
        formData.append('file', documentSource.data);
    } else {
        // For file paths, we need backend support to read from the path
        formData.append('filePath', documentSource.data);
    }
    console.log('Adding document parameters...');
    // Add other parameters
    formData.append('document_type', config.documentType || 'auto');
    formData.append('force_ai_extraction', config.forceAiExtraction ? 'true' : 'false');
    formData.append('use_batch_processing', config.useBatchProcessing ? 'true' : 'false');
    formData.append('batch_size', config.batchSize || 3);

    // NEW: Add is_knowledge_document parameter based on documentSharing setting (private means add as knowledge so no one can see it - Q&D approach for now)
    const doNotStoreDocument = config.documentSharing === 'private';
    formData.append('do_not_store', doNotStoreDocument ? 'true' : 'false');
    
    if (config.pageRange) {
        formData.append('page_range', config.pageRange);
    }
    console.log(`Form Data: ${formData}`);
    
    try {
        console.log('Calling document process api...');
        const API_PORT = await getDocumentPort(); // Fallback to 3011 if not set
        console.log(`DOC API PORT: ${API_PORT}`);

        // Create a URL object based on the current window.location
        const url = new URL('/document/process', window.location.origin);
        // Replace just the port
        url.port = API_PORT;

        const response = await fetch(url.toString(), {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.message || 'Document processing failed');
        }
        
        const result = await response.json();
        return { 
            success: result.status === 'success', 
            data: result, 
            error: result.status === 'error' ? result.message : null 
        };
    } catch (error) {
        return { success: false, error: error.message, data: {} };
    }
}

// New extract document function
async function extractDocument(config, prev_data) {
    console.log('Getting document sources...');
    // Get document source
    const documentSource = await getDocumentSource(config, prev_data);
    if (!documentSource.success) {
        return documentSource; // Return the error
    }
    console.log(`Document source data: ${documentSource.data}`);
    console.log(`Document source type: ${documentSource.type}`);
    const formData = new FormData();
    
    if (documentSource.type === 'file') {
        // If it's a file object, add it to FormData
        formData.append('file', documentSource.data);
    } else {
        // For file paths, we need backend support to read from the path
        formData.append('filePath', documentSource.data);
    }
    console.log('Adding document parameters...');
    // Add other parameters
    formData.append('document_type', config.documentType || 'auto');
    formData.append('force_ai_extraction', config.forceAiExtraction ? 'true' : 'false');
    formData.append('use_batch_processing', config.useBatchProcessing ? 'true' : 'false');
    formData.append('batch_size', config.batchSize || 3);

    const doNotStoreDocument = config.documentSharing === 'private';
    formData.append('do_not_store', doNotStoreDocument ? 'true' : 'false');
    
    if (config.pageRange) {
        formData.append('page_range', config.pageRange);
    }
    console.log(`Form Data: ${formData}`);
    
    try {
        const docExtractRoute = await getDocumentURL('/document/extract');

        const response = await fetch(docExtractRoute, {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.message || 'Document extraction failed');
        }
        
        const result = await response.json();
        return { 
            success: result.status === 'success', 
            data: result.status === 'success' ? result.extracted_data : null, 
            error: result.status === 'error' ? result.message : null 
        };
    } catch (error) {
        return { success: false, error: error.message, data: {} };
    }
}

// Analyze document with AI
async function analyzeDocument(config, prev_data) {
    // Get document source
    const documentSource = await getDocumentSource(config, prev_data);
    if (!documentSource.success) {
        return documentSource; // Return the error
    }
    
    const formData = new FormData();
    
    if (documentSource.type === 'file') {
        // If it's a file object, add it to FormData
        formData.append('file', documentSource.data);
    } else {
        // For file paths, we need backend support to read from the path
        formData.append('filePath', documentSource.data);
    }
    
    // Add prompt parameter (with variable replacement)
    const prompt = replaceVariableReferences(config.prompt, workflowVariables);
    formData.append('prompt', prompt);
    
    try {
        const docAnalyzeRoute = await getDocumentURL('/document/analyze');
        const response = await fetch(docAnalyzeRoute, {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.message || 'Document analysis failed');
        }
        
        const result = await response.json();
        return { 
            success: result.status === 'success', 
            data: result, 
            error: result.status === 'error' ? result.message : null 
        };
    } catch (error) {
        return { success: false, error: error.message, data: {} };
    }
}

// Get document by ID
async function getDocument(config, prev_data) {
    // Get document ID (with variable replacement)
    const documentId = replaceVariableReferences(config.documentId, workflowVariables);
    
    if (!documentId) {
        return { success: false, error: 'Document ID is required', data: {} };
    }
    
    // Get format
    const format = config.outputFormat === 'same' ? 'json' : config.outputFormat.toLowerCase();
    
    try {
        const response = await fetch(`/document/get/${documentId}?format=${format}`);
        
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.message || 'Failed to retrieve document');
        }
        
        // Handle different response types
        if (format === 'csv') {
            const csvText = await response.text();
            return { success: true, data: { content: csvText, format: 'csv' } };
        } else {
            const result = await response.json();
            return { 
                success: result.status === 'success', 
                data: result.data || result, 
                error: result.status === 'error' ? result.message : null 
            };
        }
    } catch (error) {
        return { success: false, error: error.message, data: {} };
    }
}

// Save document
async function saveDocument(config, prev_data) {
    // Get document content
    let content;
    
    if (config.sourceType === 'previous') {
        // Use previous output
        content = prev_data.content || prev_data.text || JSON.stringify(prev_data);
    } else if (config.sourceType === 'variable') {
        // Get from workflow variable
        const varName = config.sourcePath;
        const varValue = workflowVariables[varName];
        
        if (varValue === undefined) {
            return { 
                success: false, 
                error: `Variable "${varName}" not found`, 
                data: {} 
            };
        }
        
        content = typeof varValue === 'string' ? varValue : JSON.stringify(varValue);
    } else {
        // File path or upload not supported for content
        return { 
            success: false, 
            error: 'Source type not supported for save operation', 
            data: {} 
        };
    }
    
    console.log(`===>>> config.outputPath: ${config.outputPath}`);
    // Get output path (with variable replacement)
    const outputPath = replaceVariableReferences(config.outputPath, workflowVariables);
    console.log(`===>>> const.outputPath: ${outputPath}`);
    
    if (!outputPath) {
        return { success: false, error: 'Output path is required', data: {} };
    }
    
    try {
        const response = await fetch('/document/save', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                content: content,
                outputPath: outputPath
            })
        });
        
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.message || 'Failed to save document');
        }
        
        const result = await response.json();
        return { 
            success: result.status === 'success', 
            data: result, 
            error: result.status === 'error' ? result.message : null 
        };
    } catch (error) {
        return { success: false, error: error.message, data: {} };
    }
}

// Helper function to get document source based on config
async function getDocumentSource(config, prev_data) {
    try {
        switch (config.sourceType) {
            case 'file':
                // Get file path (with variable replacement)
                const filePath = replaceVariableReferences(config.sourcePath, workflowVariables);
                
                if (!filePath) {
                    return { success: false, error: 'File path is required', data: null };
                }
                
                return { success: true, type: 'path', data: filePath };
                
            case 'variable':
                // Get from workflow variable
                const varName = config.sourcePath;
                const varValue = workflowVariables[varName];
                
                if (varValue === undefined) {
                    return { 
                        success: false, 
                        error: `Variable "${varName}" not found`, 
                        data: null 
                    };
                }
                
                // Check if it's a file object or a path string
                if (typeof varValue === 'object' && varValue.type === 'file') {
                    return { success: true, type: 'file', data: varValue.data };
                } else if (typeof varValue === 'string') {
                    return { success: true, type: 'path', data: varValue };
                } else {
                    return { 
                        success: false, 
                        error: `Variable "${varName}" does not contain a valid file or path`, 
                        data: null 
                    };
                }
                
            case 'previous':
                // Get from previous step output
                if (prev_data.filePath) {
                    return { success: true, type: 'path', data: prev_data.filePath };
                } else if (prev_data.file) {
                    return { success: true, type: 'file', data: prev_data.file };
                } else {
                    return { 
                        success: false, 
                        error: 'Previous step output does not contain a valid file or path', 
                        data: null 
                    };
                }
                
            case 'upload':
                // Not implemented in this version - would require file upload UI in the node config
                return { 
                    success: false, 
                    error: 'Upload file source not implemented yet', 
                    data: null 
                };
                
            default:
                return { 
                    success: false, 
                    error: `Unknown source type: ${config.sourceType}`, 
                    data: null 
                };
        }
    } catch (error) {
        return { success: false, error: error.message, data: null };
    }
}


async function executeAIAction(config, node, prev_output = {}) {
    console.log('Executing AI action with config:', config);
    
    const apiUrl = '/chat/general';
    
    try {
        let processedPrompt = config.prompt.replace('{prev_output}', String(prev_output));
        console.log(`Replaced prev_output in prompt:==>> executeAIAction->prev_output->${String(prev_output)}`);
        console.log(`AI Action Config BEFORE:==>> ${formatJsonOutput(config)}`); 

        // Replace variable references in the prompt
        processedPrompt = replaceVariableReferences(processedPrompt, workflowVariables);

        // Log the processed prompt in debug mode
        if (isDebugMode) {
            addDebugLogEntry(`AI prompt after variable substitution: ${processedPrompt}`, 'info');
        }

        // Prepare the request data
        const requestData = {
            agent_id: config.agent_id,
            prompt: processedPrompt,
            hist: '[]' // Use empty array if no history provided
        };

        console.log('Sending request with data:', requestData);

        // Make the API call
        const response = await fetch(apiUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            },
            body: JSON.stringify(requestData)
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const result = await response.json();
        console.log('API response received:', result);

        if (result.status === 'error') {
            throw new Error(result.response);
        }

        // Store result in variable if configured
        console.log(`AI Action Config:==>> ${formatJsonOutput(config)}`); 
        if (isDebugMode) {
            addDebugLogEntry(`AI Action Config:==>> ${formatJsonOutput(config)}`, 'info');
        }
        // if (config.outputVariable) {
        //     const variableName = extractVariableName(config.outputVariable);
        //     workflowVariables[variableName] = (result.response ?? result.data ?? {});
            
        //     if (isDebugMode) {
        //         if (typeof result.response === 'object' && result.response !== null) {
        //             const rowCount = Array.isArray(result.response) ? result.response.length : 0;
        //             addDebugLogEntry(`Saved AI response in variable "${variableName}": ${rowCount} rows`, 'info');
        //         } else {
        //             addDebugLogEntry(`Saved AI response in variable "${variableName}"`, 'info');
        //         }
        //     }
            
        //     // Reflect change in the variables UI table if present
        //     if (typeof updateVariablesTable === 'function') {
        //         updateVariablesTable();
        //     }
        // }
        // TODO: Copy this function to the AI Action node and modify it to set the variable in the workflowVariables object
        console.log(`Output variable (AI Action): ${node._originalConfig.outputVariable}`);
        console.log(`Variable name (AI Action): ${extractVariableName(node._originalConfig.outputVariable)}`);
        if (node._originalConfig.outputVariable) {
            const analysisOutputVariable = extractVariableName(node._originalConfig.outputVariable);
            workflowVariables[analysisOutputVariable] = (result.response ?? result.data ?? {});
            
            if (isDebugMode) {
                addDebugLogEntry(`Set variable "${analysisOutputVariable}" to: ${(result.response ?? result.data ?? {})}`, 'info');
                updateVariablesTable();
            }
        }

        return {
            success: true,
            data: {
                response: result.response,
                chatHistory: result.chat_history
            }
        };

    } catch (error) {
        console.error('Error executing AI action:', error);
        return {
            success: false,
            error: error.message,
            data: {
                response: null,
                chatHistory: []
            }
        };
    }
}


// NODE CONFIG SAVE FUNCTIONS
async function saveNodeConfigV2() {
    if (!configuredNode) {
        console.error('No node selected for configuration');
        return;
    }

    const nodeType = configuredNode.getAttribute('data-type');
    const modalBody = document.getElementById('nodeConfigModalBody');
    const config = {};
    let hasValidationErrors = false;

    // Get all form inputs
    const inputs = modalBody.querySelectorAll('input, select, textarea');
    
    // Validation rules per node type
    const validationRules = {
        'Database': {
            query: (value) => {
                if (!value) return 'Query is required';
                return null;
            }
        },
        'Server': {
            url: (value) => {
                if (!value) return 'URL is required';
                try {
                    new URL(value);
                    return null;
                } catch (e) {
                    return 'Invalid URL format';
                }
            },
            headers: (value) => {
                if (value) {
                    try {
                        JSON.parse(value);
                        return null;
                    } catch (e) {
                        return 'Invalid JSON format in headers';
                    }
                }
                return null;
            }
        },
        'AI Action': {
            maxTokens: (value) => {
                if (isNaN(value) || value < 1) return 'Max tokens must be a positive number';
                return null;
            },
            temperature: (value) => {
                if (isNaN(value) || value < 0 || value > 1) return 'Temperature must be between 0 and 1';
                return null;
            }
        }
    };

    // Clear previous error states
    modalBody.querySelectorAll('.is-invalid').forEach(el => {
        el.classList.remove('is-invalid');
    });
    modalBody.querySelectorAll('.invalid-feedback').forEach(el => {
        el.remove();
    });

    // Collect and validate all form values
    inputs.forEach(input => {
        let value = input.value;
        
        // Special handling for different input types
        switch(input.type) {
            case 'number':
                value = parseFloat(value);
                break;
            case 'checkbox':
                value = input.checked;
                break;
            case 'range':
                value = parseFloat(value);
                break;
        }

        // Store the value
        config[input.name] = value;

        // Validate if rules exist for this field
        if (validationRules[nodeType]?.[input.name]) {
            const errorMessage = validationRules[nodeType][input.name](value);
            if (errorMessage) {
                hasValidationErrors = true;
                
                // Add error styling
                input.classList.add('is-invalid');
                
                // Add error message
                const errorDiv = document.createElement('div');
                errorDiv.className = 'invalid-feedback';
                errorDiv.textContent = errorMessage;
                input.parentNode.appendChild(errorDiv);
            }
        }
    });

    if (hasValidationErrors) {
        return; // Don't save if there are validation errors
    }

    try {
        // Add timestamp and user info to config
        config._lastModified = new Date().toISOString();
        config._modifiedBy = 'current_user'; // Replace with actual user info if available

        // Save the configuration
        nodeConfigs.set(configuredNode.id, config);

        // Update node appearance or state based on configuration
        updateNodeAppearance(configuredNode, config);

        // Hide modal
        configModal.hide();

        // Show success message
        showToast('Configuration saved successfully', 'success');
        
        // Optional: Save to backend
        await saveConfigToBackend(configuredNode.id, config);

    } catch (error) {
        console.error('Error saving configuration:', error);
        showToast('Error saving configuration: ' + error.message, 'error');
    }
}

// Helper function to update node appearance based on configuration
function updateNodeAppearance(node, config) {
    const nodeType = node.getAttribute('data-type');
    
    switch(nodeType) {
        case 'Database':
            // Add a small badge showing the database type
            let badgeEl = node.querySelector('.config-badge');
            if (!badgeEl) {
                badgeEl = document.createElement('div');
                badgeEl.className = 'config-badge';
                node.appendChild(badgeEl);
            }
            badgeEl.textContent = config.dbType;
            break;

        case 'AI Action':
            // Add model info to the node
            let modelBadgeEl = node.querySelector('.config-badge');
            if (!modelBadgeEl) {
                modelBadgeEl = document.createElement('div');
                modelBadgeEl.className = 'config-badge';
                node.appendChild(modelBadgeEl);
            }
            modelBadgeEl.textContent = config.model;
            break;
            
        // Add cases for other node types
    }

    // Add a visual indicator that the node is configured
    node.classList.add('configured');
}

// Helper function to save configuration to backend
async function saveConfigToBackend(nodeId, config) {
    try {
        const response = await fetch('/api/workflow/node-config', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                nodeId: nodeId,
                config: config
            })
        });

        if (!response.ok) {
            throw new Error('Failed to save configuration to server');
        }

        return await response.json();
    } catch (error) {
        console.error('Error saving to backend:', error);
        throw error; // Re-throw to handle in the calling function
    }
}

// Helper function to show toast messages
function showToast(message, type = 'info') {
    // Create toast container if it doesn't exist
    let toastContainer = document.getElementById('toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.id = 'toast-container';
        toastContainer.className = 'position-fixed bottom-0 end-0 p-3';
        document.body.appendChild(toastContainer);
    }

    // Create toast
    const toastElement = document.createElement('div');
    toastElement.className = `toast align-items-center text-white bg-${type}`;
    toastElement.setAttribute('role', 'alert');
    toastElement.setAttribute('aria-live', 'assertive');
    toastElement.setAttribute('aria-atomic', 'true');

    toastElement.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">
                ${message}
            </div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
    `;

    toastContainer.appendChild(toastElement);

    // Initialize and show the toast
    const toast = new bootstrap.Toast(toastElement, { delay: 3000 });
    toast.show();

    // Remove the toast element after it's hidden
    toastElement.addEventListener('hidden.bs.toast', () => {
        toastElement.remove();
    });
}


/***** Workflow management functions *****/

// Function to open the workflow manager
function openWorkflowManager() {
    refreshWorkflowsList().then(() => {
        workflowManagerModal.show();
    });
}

// Function to load and populate categories
async function loadCategories() {
    try {
        const response = await fetch('/get/workflow/categories');
        const data = await response.json();
        
        // Convert data to array if it's not already
        let categories = [];
        if (typeof data === 'string') {
            categories = JSON.parse(data);
        } else if (Array.isArray(data)) {
            categories = data;
        } else if (typeof data === 'object') {
            categories = Object.values(data);
        }
        
        // Clear and populate the category map
        categoryMap.clear();
        categories.forEach(cat => {
            const id = cat.id || cat.ID;
            const name = cat.name || cat.NAME;
            categoryMap.set(id.toString(), name);
        });
        
        const select = document.getElementById('categoryFilter');
        select.innerHTML = '<option value="">All Categories</option>';
        
        // Add uncategorized option
        //select.innerHTML += '<option value="null">Uncategorized</option>';
        
        // Add categories with ID as value
        categories.forEach(category => {
            const id = category.id || category.ID;
            const name = category.name || category.NAME;
            if (name) {
                const option = document.createElement('option');
                option.value = id; // Use ID as value
                option.textContent = name; // Display name as text
                select.appendChild(option);
            }
        });

        // Add change event listener
        select.addEventListener('change', filterWorkflows);
        
    } catch (error) {
        console.error('Error loading categories:', error);
    }
}

// Add a debug function to help see the exact data structure
function debugCategoryData(categories) {
    console.log('Categories type:', typeof categories);
    console.log('Categories structure:', JSON.stringify(categories, null, 2));
    if (typeof categories === 'object') {
        console.log('Keys:', Object.keys(categories));
        console.log('First item:', categories[Object.keys(categories)[0]]);
    }
}

// Function to populate the workflows dropdown with category grouping
async function populateWorkflowsDropdown() {
    const select = document.getElementById('workflowSelect');
    if (!select) return;

    try {
        // Show loading state
        select.disabled = true;
        select.innerHTML = '<option value="">Loading workflows...</option>';
        
        // Get workflows from server
        const workflowsResponse = await fetch('/get/workflows');
        if (!workflowsResponse.ok) {
            throw new Error('Failed to load workflows');
        }
        
        // Get categories from server
        const categoriesResponse = await fetch('/get/workflow/categories');
        if (!categoriesResponse.ok) {
            throw new Error('Failed to load categories');
        }
        
        const workflowsData = await workflowsResponse.json();
        const categoriesData = await categoriesResponse.json();
        
        // Parse the data
        let workflows = typeof workflowsData === 'string' ? JSON.parse(workflowsData) : workflowsData;
        let categories = typeof categoriesData === 'string' ? JSON.parse(categoriesData) : categoriesData;
        
        if (!Array.isArray(workflows)) {
            workflows = Object.values(workflows);
        }
        
        if (!Array.isArray(categories)) {
            categories = Object.values(categories);
        }
        
        // Create category map for lookups
        const categoryMap = new Map();
        categories.forEach(category => {
            const id = category.id || category.ID;
            const name = category.name || category.NAME;
            categoryMap.set(id.toString(), name);
        });
        
        // Group workflows by category
        const workflowsByCategory = {};
        const uncategorizedWorkflows = [];
        
        workflows.forEach(workflow => {
            const workflowName = workflow.workflow_name || workflow.name || 'Unnamed';
            const workflowId = workflow.id || workflow.ID;
            const categoryId = workflow.category_id || workflow.CATEGORY_ID;
            
            if (categoryId && categoryMap.has(categoryId.toString())) {
                const categoryName = categoryMap.get(categoryId.toString());
                
                if (!workflowsByCategory[categoryName]) {
                    workflowsByCategory[categoryName] = [];
                }
                
                workflowsByCategory[categoryName].push({
                    id: workflowId,
                    name: workflowName
                });
            } else {
                uncategorizedWorkflows.push({
                    id: workflowId,
                    name: workflowName
                });
            }
        });
        
        // Reset dropdown
        select.innerHTML = '<option value="">Select a workflow...</option>';
        
        // Add uncategorized workflows first
        if (uncategorizedWorkflows.length > 0) {
            const uncategorizedGroup = document.createElement('optgroup');
            uncategorizedGroup.label = 'Uncategorized';
            
            uncategorizedWorkflows.forEach(workflow => {
                const option = document.createElement('option');
                option.value = workflow.id;
                option.textContent = workflow.name;
                uncategorizedGroup.appendChild(option);
            });
            
            select.appendChild(uncategorizedGroup);
        }
        
        // Add workflows grouped by category
        Object.entries(workflowsByCategory).forEach(([categoryName, categoryWorkflows]) => {
            const group = document.createElement('optgroup');
            group.label = categoryName;
            
            categoryWorkflows.forEach(workflow => {
                const option = document.createElement('option');
                option.value = workflow.id;
                option.textContent = workflow.name;
                group.appendChild(option);
            });
            
            select.appendChild(group);
        });

    } catch (error) {
        console.error('Error populating workflows dropdown:', error);
        select.innerHTML = '<option value="">Error loading workflows</option>';
    } finally {
        select.disabled = false;
    }
}

// Helper function to debug the data structure
function debugWorkflowData(workflows) {
    console.log('Workflows type:', typeof workflows);
    console.log('Workflows structure:', JSON.stringify(workflows, null, 2));
    if (typeof workflows === 'object') {
        console.log('Keys:', Object.keys(workflows));
        console.log('First item:', workflows[Object.keys(workflows)[0]]);
    }
}

// Function to refresh the workflows list
// Updated refresh function
async function refreshWorkflowsList() {
    // Clear any cached workflows
    cachedWorkflows = null;
    
    try {
        // Refresh both the dropdown and the table if it exists
        await populateWorkflowsDropdown();
        
        // If the workflow manager is open, refresh the table too
        const tableBody = document.getElementById('workflowTableBody');
        if (tableBody) {
            const workflows = await loadWorkflowsList();
            console.log('Workflows for table:', workflows);
            await populateWorkflowTable(workflows);
        }
    } catch (error) {
        console.error('Error refreshing workflows:', error);
        showToast('Error refreshing workflows list', 'error');
    }
}

// Enhanced workflow loading function
// async function loadWorkflowsList() {
//     try {
//         const response = await fetch('/get/workflows');
//         if (!response.ok) throw new Error('Failed to load workflows');
        
//         const workflows = await response.json();
//         return workflows;
//     } catch (error) {
//         console.error('Error loading workflows:', error);
//         throw error;
//     }
// }


// Function to load workflows list
async function loadWorkflowsList() {
    try {
        const response = await fetch('/get/workflows');
        if (!response.ok) {
            throw new Error('Failed to load workflows');
        }
        
        const data = await response.json();
        // Parse the JSON string if it's returned as a string
        const workflows = typeof data === 'string' ? JSON.parse(data) : data;
        
        console.log('Raw workflows data:', workflows);
        return workflows;
    } catch (error) {
        console.error('Error loading workflows:', error);
        throw error;
    }
}

// Update the populateWorkflowTable function to include data-workflow-id
// Update populateWorkflowTable to include category ID
async function populateWorkflowTable(data) {
    const tbody = document.getElementById('workflowTableBody');
    if (!tbody) return;

    try {
        tbody.innerHTML = '';
        
        const workflows = Array.isArray(data) ? data : Object.values(data);
        
        if (workflows.length === 0) {
            const row = document.createElement('tr');
            row.innerHTML = '<td colspan="5" class="text-center">No workflows found</td>';
            tbody.appendChild(row);
            return;
        }

        workflows.forEach(workflow => {
            const row = document.createElement('tr');
            row.setAttribute('data-workflow-id', workflow.id || workflow.ID);
            row.setAttribute('data-category-id', workflow.category_id || workflow.CATEGORY_ID || '');
            
            const categoryName = workflow.category_id ? 
                               (categoryMap.get(workflow.category_id.toString()) || 'Unknown') : 
                               'Uncategorized';
            
            const created = workflow.created_date ? new Date(workflow.created_date).toLocaleString() : 'N/A';
            const modified = workflow.last_modified ? new Date(workflow.last_modified).toLocaleString() : 'N/A';
            
            row.innerHTML = `
                <td>
                    <span class="workflow-name">${workflow.workflow_name || workflow.name || 'Unnamed'}</span>
                    <input type="text" class="form-control d-none workflow-name-input" value="${workflow.workflow_name || workflow.name || ''}">
                </td>
                <td class="workflow-category">${categoryName}</td>
                <td>${created}</td>
                <td>${modified}</td>
                <td>
                    <div class="btn-group btn-group-sm">
                        <button class="btn btn-outline-primary" onclick="renameWorkflow('${workflow.id || workflow.ID}', this)" title="Rename">
                            <i class="bi bi-pencil"></i>
                        </button>
                        <button class="btn btn-outline-info" onclick="editWorkflowCategory('${workflow.id || workflow.ID}')">
                            <i class="bi bi-tag"></i>
                        </button>
                        <button class="btn btn-outline-secondary" onclick="copyWorkflow('${workflow.id || workflow.ID}')" title="Copy Workflow">
                            <i class="bi bi-files"></i>
                        </button>
                        <button class="btn btn-outline-success" onclick="exportWorkflowById('${workflow.id || workflow.ID}')" title="Export to JSON">
                            <i class="bi bi-download"></i>
                        </button>
                        <button class="btn btn-outline-danger" onclick="deleteWorkflow('${workflow.id || workflow.ID}')" title="Delete">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                </td>
            `;
            
            tbody.appendChild(row);
        });
    } catch (error) {
        console.error('Error populating workflow table:', error);
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-danger">Error loading workflows</td></tr>';
    }
}

// Function to filter workflows
/*
function filterWorkflows() {
    const searchTerm = document.getElementById('workflowSearch').value.toLowerCase();
    const categoryFilter = document.getElementById('categoryFilter').value;
    const rows = document.querySelectorAll('#workflowTableBody tr');
    
    rows.forEach(row => {
        const name = row.querySelector('.workflow-name').textContent.toLowerCase();
        const category = row.cells[1].textContent;
        
        const matchesSearch = name.includes(searchTerm);
        const matchesCategory = !categoryFilter || category === categoryFilter;
        
        row.style.display = matchesSearch && matchesCategory ? '' : 'none';
    });
}
*/
function filterWorkflows() {
    const searchTerm = document.getElementById('workflowSearch').value.toLowerCase();
    const categoryFilter = document.getElementById('categoryFilter').value;
    const rows = document.querySelectorAll('#workflowTableBody tr');
    
    console.log('Filtering with category ID:', categoryFilter);

    rows.forEach(row => {
        const name = row.querySelector('.workflow-name').textContent.toLowerCase();
        const categoryCell = row.querySelector('.workflow-category');
        const categoryId = row.getAttribute('data-category-id');
        
        console.log('Row category ID:', categoryId);

        const matchesSearch = name.includes(searchTerm);
        const matchesCategory = !categoryFilter || // Show all if no category selected
                              (categoryFilter === 'null' && !categoryId) || // Uncategorized
                              categoryFilter === categoryId; // Direct ID match
        
        row.style.display = matchesSearch && matchesCategory ? '' : 'none';
    });
}

// Function to rename a workflow
async function renameWorkflow(workflowId, button) {
    const row = button.closest('tr');
    const nameSpan = row.querySelector('.workflow-name');
    const nameInput = row.querySelector('.workflow-name-input');
    
    if (nameInput.classList.contains('d-none')) {
        // Show input
        nameSpan.classList.add('d-none');
        nameInput.classList.remove('d-none');
        nameInput.focus();
        
        // Handle input confirmation
        nameInput.onblur = async () => {
            try {
                const newName = nameInput.value.trim();
                if (newName && newName !== nameSpan.textContent) {
                    const response = await fetch(`/api/workflows/${workflowId}/rename`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ name: newName })
                    });
                    
                    if (!response.ok) throw new Error('Failed to rename workflow');
                    
                    nameSpan.textContent = newName;
                    showToast('Workflow renamed successfully', 'success');
                    
                    // Update dropdown
                    refreshWorkflowsList();
                }
            } catch (error) {
                console.error('Error renaming workflow:', error);
                showToast('Error renaming workflow', 'error');
            } finally {
                nameSpan.classList.remove('d-none');
                nameInput.classList.add('d-none');
            }
        };
    }
}

// Function to edit workflow category
// Fixed editWorkflowCategory function
async function editWorkflowCategory(workflowId) {
    try {
        // Fetch and parse categories
        const response = await fetch('/get/workflow/categories');
        let data = await response.json();
        
        // Convert data to array if it's not already
        let categories = [];
        if (typeof data === 'string') {
            // If data is a JSON string
            categories = JSON.parse(data);
        } else if (Array.isArray(data)) {
            // If data is already an array
            categories = data;
        } else if (typeof data === 'object') {
            // If data is an object (like from pandas DataFrame)
            categories = Object.values(data);
        }

        console.log('Categories data:', categories); // Debug log
        
        // Find the row and get the category
        const row = document.querySelector(`tr[data-workflow-id="${workflowId}"]`);
        if (!row) {
            throw new Error('Workflow row not found');
        }
        
        const categoryCell = row.querySelector('.workflow-category');
        if (!categoryCell) {
            throw new Error('Category cell not found');
        }
        
        const currentCategory = categoryCell.textContent.trim();
        
        // Create the options HTML
        //let optionsHtml = '<option value="">Uncategorized</option>';
        let optionsHtml = ''
        
        // Add options for each category
        categories.forEach(cat => {
            const catId = cat.id || cat.ID;
            const catName = cat.name || cat.NAME;
            optionsHtml += `
                <option value="${catId}" ${currentCategory === catName ? 'selected' : ''}>
                    ${catName}
                </option>
            `;
        });
        
        // Create and show category selection modal
        const html = `
            <div class="modal-body">
                <select class="form-select" id="categorySelect">
                    ${optionsHtml}
                </select>
            </div>
        `;
        
        const result = await showConfirmDialog('Select Category', html);
        if (result) {
            const categoryId = document.getElementById('categorySelect').value;
            console.log(`Updating category for workflow: ${workflowId}`);
            const response = await fetch(`/update/workflows/${workflowId}/category`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ categoryId })
            });
            
            if (!response.ok) throw new Error('Failed to update category');
            
            showToast('Category updated successfully', 'success');
            refreshWorkflowsList();
        }
    } catch (error) {
        console.error('Error updating category:', error);
        showToast('Error updating category: ' + error.message, 'error');
    }
}

// Function to delete a workflow
async function deleteWorkflow(workflowId) {
    try {
        const confirmed = await showConfirmDialog(
            'Delete Workflow',
            'Are you sure you want to delete this workflow? This action cannot be undone.'
        );
        
        if (confirmed) {
            const response = await fetch(`/delete/workflow/${workflowId}`, {
                method: 'DELETE'
            });
            
            if (!response.ok) throw new Error('Failed to delete workflow');
            
            showToast('Workflow deleted successfully', 'success');
            refreshWorkflowsList();
        }
    } catch (error) {
        console.error('Error deleting workflow:', error);
        showToast('Error deleting workflow', 'error');
    }
}

// Utility function to show a confirmation dialog
function showConfirmDialog(title, content) {
    return new Promise((resolve) => {
        const modal = document.createElement('div');
        modal.className = 'modal fade';
        modal.innerHTML = `
            <div class="modal-dialog">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">${title}</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">${content}</div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                        <button type="button" class="btn btn-primary confirm-button">Confirm</button>
                    </div>
                </div>
            </div>
        `;
        
        document.body.appendChild(modal);
        const modalInstance = new bootstrap.Modal(modal);
        
        modal.querySelector('.confirm-button').onclick = () => {
            modalInstance.hide();
            resolve(true);
        };
        
        modal.addEventListener('hidden.bs.modal', () => {
            modal.remove();
            resolve(false);
        });
        
        modalInstance.show();
    });
}

// Function to load selected workflow
async function loadSelectedWorkflow() {
    const select = document.getElementById('workflowSelect');
    const workflowId = select.value;
    
    if (!workflowId) {
        showToast('Please select a workflow to load', 'warning');
        return;
    }

    console.log('Selected Workflow ID:', workflowId);

    try {
        // Show loading state
        select.disabled = true;
        document.getElementById('loadWorkflowBtn').disabled = true;
        
        // Fetch the workflow data
        const response = await fetch(`/get/workflow/${workflowId}`);
        
        if (!response.ok) {
            throw new Error('Failed to load workflow');
        }

        currentWorkflowName = select.options[select.selectedIndex].text;  // Set the current workflow name global var
        console.log(`Current workflow name: ${currentWorkflowName}`);

        // Update the workflow name display
        updateCurrentWorkflowDisplay();
        
        const workflowData = await response.json();
        // Proper debugging of the object
        console.log('Raw Selected Workflow Data:', workflowData);
        console.log('Workflow Data Type:', typeof workflowData);
        console.log('Workflow Data Structure:', JSON.stringify(workflowData, null, 2));

        // Clear existing workflow
        clearWorkflow();

        // Parse the data if it's a string
        const parsedData = typeof workflowData === 'string' ? JSON.parse(workflowData) : workflowData;

        // Handle the workflow data structure
        if (parsedData.workflow_data) {
            // If the workflow data is nested under a key
            loadWorkflow(parsedData.workflow_data);
            // Load variables from the workflow data
            setWorkflowVariablesFromJson(parsedData.workflow_data);
        } else if (parsedData.nodes && parsedData.connections) {
            // If the data is already in the expected format
            loadWorkflow(parsedData);
            // Load variables from the workflow data
            setWorkflowVariablesFromJson(parsedData);
        } else {
            throw new Error('Invalid workflow data structure');
        }
        
        showToast('Workflow loaded successfully', 'success');
    } catch (error) {
        console.error('Error loading workflow:', error);
        showToast('Error loading workflow: ' + error.message, 'error');
        // Clear current workflow name on error
        currentWorkflowName = null;
        updateCurrentWorkflowDisplay();
    } finally {
        select.disabled = false;
        document.getElementById('loadWorkflowBtn').disabled = false;
    }
}

// Helper function to clear the current workflow
function clearWorkflow() {
    // Clear existing workflow
        const canvas = document.getElementById('workflow-canvas');
        if (canvas) {
            canvas.innerHTML = '';
            jsPlumbInstance.reset();  // This clears ALL event bindings!
        }
        startNode = null;
        nodeConfigs.clear();

        // CRITICAL: Re-setup context menus after clearing
        // Use setTimeout to ensure jsPlumb is fully reset
        setTimeout(() => {
            bindContextMenuToAllConnections();
            console.log('Context menus re-initialized after clearing workflow');
        }, 500);
}

// Helper function to show toast messages if not already defined
function showToast(message, type = 'info') {
    // Create toast container if it doesn't exist
    let toastContainer = document.getElementById('toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.id = 'toast-container';
        toastContainer.className = 'position-fixed bottom-0 end-0 p-3';
        document.body.appendChild(toastContainer);
    }

    // Create toast
    const toastElement = document.createElement('div');
    toastElement.className = `toast align-items-center text-white bg-${type}`;
    toastElement.setAttribute('role', 'alert');
    toastElement.setAttribute('aria-live', 'assertive');
    toastElement.setAttribute('aria-atomic', 'true');

    toastElement.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">
                ${message}
            </div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
    `;

    toastContainer.appendChild(toastElement);

    // Initialize and show the toast
    const toast = new bootstrap.Toast(toastElement, { delay: 3000 });
    toast.show();

    // Remove the toast element after it's hidden
    toastElement.addEventListener('hidden.bs.toast', () => {
        toastElement.remove();
    });
}


function initializeDebugPanel() {
    // Add event listener for debug panel header click (excluding the button area)
    const debugHeader = document.querySelector('.debug-header');
    if (debugHeader) {
        debugHeader.addEventListener('click', function(e) {
            // Check if click is on the button or any of its children
            const toggleBtn = e.target.closest('.btn');
            
            // If clicking on the toggle button, let the onclick handler handle it
            if (toggleBtn && toggleBtn.getAttribute('onclick') && toggleBtn.getAttribute('onclick').includes('toggleDebugPanel')) {
                return; // Let the button's onclick handler work
            }
            
            // Otherwise, if clicking elsewhere on the header, toggle the panel
            toggleDebugPanel();
        });
    }
    
    // Initialize in collapsed state
    toggleDebugPanel(true);
}

// Toggle debug panel visibility
function toggleDebugPanel(forceCollapse) {
    const debugPanel = document.getElementById('debug-panel');
    const toggleIcon = document.getElementById('debug-toggle-icon');
    
    if (forceCollapse === true || !debugPanel.classList.contains('collapsed')) {
        debugPanel.classList.add('collapsed');
        toggleIcon.classList.remove('bi-chevron-down');
        toggleIcon.classList.add('bi-chevron-up');
        debugPanelCollapsed = true;
    } else {
        debugPanel.classList.remove('collapsed');
        toggleIcon.classList.remove('bi-chevron-up');
        toggleIcon.classList.add('bi-chevron-down');
        debugPanelCollapsed = false;
    }
}

// Enable or disable debug mode
function enableDebugMode(enable) {
    isDebugMode = enable;
    const debugBtn = document.getElementById('enable-debug-btn');
    const statusElement = document.getElementById('workflow-debug-status');
    
    if (enable) {
        //debugBtn.classList.remove('btn-outline-primary');
        //debugBtn.classList.add('btn-primary');
        //debugBtn.innerHTML = '<i class="bi bi-bug"></i> Debug Enabled';
        statusElement.textContent = 'Ready';
        statusElement.parentElement.classList.remove('bg-secondary');
        statusElement.parentElement.classList.add('bg-primary');
        
        // Show debug panel if it's collapsed
        if (debugPanelCollapsed) {
            toggleDebugPanel();
        }
        
        addDebugLogEntry('Debug mode enabled', 'info');
    } else {
        debugBtn.classList.remove('btn-primary');
        debugBtn.classList.add('btn-outline-primary');
        debugBtn.innerHTML = '<i class="bi bi-bug"></i> Enable Debug';
        statusElement.textContent = 'Not Running';
        statusElement.parentElement.classList.remove('bg-primary');
        statusElement.parentElement.classList.add('bg-secondary');
        
        addDebugLogEntry('Debug mode disabled', 'info');
    }
}

// Add a log entry to the debug console
function addDebugLogEntry(message, type = 'default') {
    const logContent = document.getElementById('debug-log-content');
    if (!logContent) return;
    
    const entry = document.createElement('div');
    entry.className = `debug-log-entry log-${type}`;
    
    const timestamp = new Date().toLocaleTimeString('en-US', { 
        hour12: false, 
        hour: '2-digit', 
        minute: '2-digit', 
        second: '2-digit' 
    });
    
    // Optional: Add icons for better visual distinction
    let icon = '';
    switch(type) {
        case 'error': icon = '❌ '; break;
        case 'warning': icon = '⚠️ '; break;
        case 'success': icon = '✅ '; break;
        case 'info': icon = 'ℹ️ '; break;
        case 'debug': icon = '🐛 '; break;
        default: icon = '▶ ';
    }
    
    entry.innerHTML = `
        <span class="timestamp" style="font-weight: 600; margin-right: 8px; color: #495057; font-size: 0.8rem;">${timestamp}</span>
        <span class="message">${icon}${message}</span>
    `;
    
    logContent.appendChild(entry);
    logContent.scrollTop = logContent.scrollHeight;
}

// Update the debug log UI
function updateDebugLog() {
    const logContent = document.getElementById('debug-log-content');
    if (!logContent) return;
    
    let html = '';
    
    debugLogEntries.forEach(entry => {
        html += `<div class="debug-log-entry debug-log-${entry.type}">
            <span class="debug-log-time">[${entry.timestamp}]</span> 
            <span class="debug-log-message">${escapeHtml(entry.message)}</span>
        </div>`;
    });
    
    logContent.innerHTML = html;
    
    // Scroll to bottom
    logContent.scrollTop = logContent.scrollHeight;
}

// Clear debug logs
function clearDebugLogs() {
    debugLogEntries = [];
    nodeOutputs = {};
    executionPath = [];
    
    // Update UI
    updateDebugLog();
    updateNodeOutputSelect();
    updateExecutionPath();
    
    addDebugLogEntry('Debug logs cleared', 'info');
}

function clearDebugPanelData() {
    // Clear debug logs and outputs
    debugLogEntries = [];
    nodeOutputs = {};
    executionPath = [];
    
    // Clear workflow variables
    workflowVariables = {};
    workflowVariableDefinitions = {};
    
    // Clear any tracking variables
    if (window.recentlyChangedVariables) {
        window.recentlyChangedVariables.clear();
    }
    if (window.lastServerVariables) {
        window.lastServerVariables = null;
    }
    if (window.lastLoggedVariableChanges) {
        window.lastLoggedVariableChanges = null;
    }
    
    // Update all UI components
    updateDebugLog();
    updateNodeOutputSelect();
    updateExecutionPath();
    updateVariablesTable();
    
    // Reset debug status
    // const debugStatus = document.getElementById('workflow-debug-status');
    // if (debugStatus) {
    //     debugStatus.textContent = 'Not Running';
    //     debugStatus.parentElement.classList.remove('bg-running', 'bg-success', 'bg-error');
    //     debugStatus.parentElement.classList.add('bg-secondary');
    // }
}

// Escape HTML to prevent XSS
function escapeHtml(unsafe) {
    return unsafe
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// Format object as JSON with indentation
function formatJsonOutput(obj) {
    try {
        if (typeof obj === 'string') {
            // Try to parse as JSON first
            try {
                return JSON.stringify(JSON.parse(obj), null, 2);
            } catch (e) {
                return obj;
            }
        } else {
            return JSON.stringify(obj, null, 2);
        }
    } catch (e) {
        return String(obj);
    }
}

// Update the node output selector dropdown
function updateNodeOutputSelect() {
    const select = document.getElementById('node-output-select');
    if (!select) return;
    
    // Clear current options
    select.innerHTML = '<option value="">Select a node...</option>';
    
    // Add option for each node with output
    Object.keys(nodeOutputs).forEach(nodeId => {
        const node = document.getElementById(nodeId);
        if (node) {
            const nodeName = node.querySelector('.node-content').textContent.trim();
            const option = document.createElement('option');
            option.value = nodeId;
            option.textContent = nodeName;
            select.appendChild(option);
        }
    });
}

// Show the selected node's output
function showSelectedNodeOutput() {
    const select = document.getElementById('node-output-select');
    const outputContent = document.getElementById('node-output-content');
    
    if (!select || !outputContent) return;
    
    const nodeId = select.value;
    if (!nodeId) {
        outputContent.textContent = 'No node selected';
        return;
    }
    
    const output = nodeOutputs[nodeId];
    if (output) {
        outputContent.textContent = formatJsonOutput(output);
    } else {
        outputContent.textContent = 'No output available for this node';
    }
}

// Update the execution path list
function updateExecutionPath() {
    const pathList = document.getElementById('execution-path-list');
    if (!pathList) return;
    
    pathList.innerHTML = '';
    
    executionPath.forEach((step, index) => {
        const li = document.createElement('li');
        li.className = `list-group-item execution-path-item ${step.success ? 'success' : 'error'}`;
        
        const nodeElement = document.getElementById(step.nodeId);
        const nodeName = nodeElement ? 
            nodeElement.querySelector('.node-content').textContent.trim() :
            step.nodeId;
        
        li.innerHTML = `
            <div class="d-flex justify-content-between align-items-center">
                <div>
                    <span class="badge ${step.success ? 'bg-success' : 'bg-danger'}">${index + 1}</span>
                    <strong>${nodeName}</strong>
                </div>
                <span class="text-muted small">${step.timestamp}</span>
            </div>
            <div class="mt-1 small">
                ${step.message}
            </div>
        `;
        
        pathList.appendChild(li);
    });
    
    // If empty, show message
    if (executionPath.length === 0) {
        const li = document.createElement('li');
        li.className = 'list-group-item text-center text-muted';
        li.textContent = 'No execution steps recorded yet';
        pathList.appendChild(li);
    }
}

// Update the variables table
function updateVariablesTable() {
    const tableBody = document.getElementById('variables-table-body');
    if (!tableBody) return;
    
    tableBody.innerHTML = '';
    
    // Add row for each variable
    Object.entries(workflowVariables).forEach(([name, value]) => {
        const tr = document.createElement('tr');
        
        // Get type from definition if available
        const definition = workflowVariableDefinitions[name] || {};
        const type = definition.type || typeof value;
        
        tr.innerHTML = `
            <td>${name}</td>
            <td>${type}</td>
            <td class="text-break">${formatVariableValue(value)}</td>
        `;
        
        tableBody.appendChild(tr);
    });
    
    // If empty, show message
    if (Object.keys(workflowVariables).length === 0) {
        const tr = document.createElement('tr');
        tr.innerHTML = '<td colspan="3" class="text-center text-muted">No variables defined yet</td>';
        tableBody.appendChild(tr);
    }
}

// Format variable value for display
function formatVariableValue(value) {
    if (value === null) return '<em class="text-muted">null</em>';
    if (value === undefined) return '<em class="text-muted">undefined</em>';
    
    if (typeof value === 'object') {
        try {
            // Limit length for display
            const json = JSON.stringify(value);
            if (json.length > 100) {
                return escapeHtml(json.substring(0, 100)) + '...';
            }
            return escapeHtml(json);
        } catch (e) {
            return String(value);
        }
    }
    
    if (typeof value === 'string' && value.length > 100) {
        return escapeHtml(value.substring(0, 100)) + '...';
    }
    
    return escapeHtml(String(value));
}



// Workflow Variables Management Functions

// Open the workflow variables modal
function openWorkflowVariables() {
    //loadWorkflowVariableDefinitions();
    populateWorkflowVariablesTable();
    const modal = new bootstrap.Modal(document.getElementById('workflowVariablesModal'));
    modal.show();
}

// Show the add variable form
function showAddVariableForm() {
    const form = document.getElementById('add-variable-form');
    if (form) {
        form.style.display = 'block';
        
        // Update title and button based on mode
        const titleElement = form.querySelector('.card-title');
        const buttonElement = form.querySelector('.btn-primary[onclick="addWorkflowVariable()"]');
        
        if (isEditingVariable) {
            if (titleElement) titleElement.textContent = 'Edit Variable';
            if (buttonElement) buttonElement.textContent = 'Update Variable';
        } else {
            if (titleElement) titleElement.textContent = 'New Variable';
            if (buttonElement) buttonElement.textContent = 'Add Variable';
        }
        
        document.getElementById('var-name').focus();
    }
}

// Hide the add variable form
function hideAddVariableForm() {
    const form = document.getElementById('add-variable-form');
    if (form) {
        form.style.display = 'none';
        
        // Clear form fields
        document.getElementById('var-name').value = '';
        document.getElementById('var-value').value = '';
        document.getElementById('var-description').value = '';
        document.getElementById('var-type').value = 'string';
        
        // Re-enable the name field and reset edit mode
        document.getElementById('var-name').disabled = false;
        isEditingVariable = false;
        originalVariableName = null;
    }
}

// Add a new workflow variable
function addWorkflowVariable() {
    const nameInput = document.getElementById('var-name');
    const typeInput = document.getElementById('var-type');
    const valueInput = document.getElementById('var-value');
    const descriptionInput = document.getElementById('var-description');
    
    const name = nameInput.value.trim();
    const type = typeInput.value;
    const defaultValue = valueInput.value;
    const description = descriptionInput.value.trim();
    
    // Validation
    if (!name) {
        showToast('Variable name is required', 'warning');
        nameInput.focus();
        return;
    }
    
    // Variable name validation
    if (!/^[a-zA-Z][a-zA-Z0-9_]*$/.test(name)) {
        showToast('Variable name must start with a letter and contain only letters, numbers, and underscores', 'warning');
        nameInput.focus();
        return;
    }
    
    // Check for duplicates only if we're adding (not editing) or if the name changed
    if (!isEditingVariable || (isEditingVariable && name !== originalVariableName)) {
        if (workflowVariableDefinitions[name]) {
            showToast(`Variable "${name}" already exists`, 'warning');
            nameInput.focus();
            return;
        }
    }
    
    // If editing and name changed, delete the old variable
    if (isEditingVariable && name !== originalVariableName) {
        delete workflowVariableDefinitions[originalVariableName];
    }
    
    // Add or update the variable
    workflowVariableDefinitions[name] = {
        type,
        defaultValue,
        description
    };
    
    // Save to localStorage
    saveWorkflowVariableDefinitions();
    
    // Update UI
    populateWorkflowVariablesTable();
    
    // Hide the form
    hideAddVariableForm();
    
    // Show success message
    const action = isEditingVariable ? 'updated' : 'added';
    showToast(`Variable "${name}" ${action} successfully`, 'success');
}


// Edit an existing workflow variable
function editWorkflowVariable(name) {
    const variable = workflowVariableDefinitions[name];
    if (!variable) return;
    
    // Set edit mode
    isEditingVariable = true;
    originalVariableName = name;
    
    // Populate the form
    document.getElementById('var-name').value = name;
    document.getElementById('var-type').value = variable.type || 'string';
    document.getElementById('var-value').value = variable.defaultValue || '';
    document.getElementById('var-description').value = variable.description || '';
    
    // Show the form (this will update the title and button text)
    showAddVariableForm();
    
    // Disable the name field to prevent changing the key
    document.getElementById('var-name').disabled = true;
}

// Delete a workflow variable
function deleteWorkflowVariable(name) {
    if (!workflowVariableDefinitions[name]) return;
    
    if (!confirm(`Are you sure you want to delete the variable "${name}"?`)) {
        return;
    }
    
    // Remove the variable
    delete workflowVariableDefinitions[name];
    
    // Save to localStorage
    saveWorkflowVariableDefinitions();
    
    // Update UI
    populateWorkflowVariablesTable();
    
    // Show success message
    showToast(`Variable "${name}" deleted successfully`, 'success');
}

// Populate the workflow variables table
function populateWorkflowVariablesTable() {
    const tableBody = document.getElementById('workflowVariablesTableBody');
    if (!tableBody) return;
    
    tableBody.innerHTML = '';
    
    const variables = Object.entries(workflowVariableDefinitions);
    
    if (variables.length === 0) {
        const row = document.createElement('tr');
        row.innerHTML = '<td colspan="5" class="text-center">No variables defined yet</td>';
        tableBody.appendChild(row);
        return;
    }
    
    variables.forEach(([name, variable]) => {
        const row = document.createElement('tr');
        
        row.innerHTML = `
            <td>${name}</td>
            <td>${variable.type || 'string'}</td>
            <td>${formatVariableValueForTable(variable.defaultValue, variable.type)}</td>
            <td>${variable.description || ''}</td>
            <td>
                <div class="btn-group btn-group-sm">
                    <button class="btn btn-outline-primary" onclick="editWorkflowVariable('${name}')">
                        <i class="bi bi-pencil"></i>
                    </button>
                    <button class="btn btn-outline-danger" onclick="deleteWorkflowVariable('${name}')">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
            </td>
        `;
        
        tableBody.appendChild(row);
    });
}

// Format variable value for table display
function formatVariableValueForTable(value, type) {
    if (value === undefined || value === null || value === '') {
        return '<em class="text-muted">Empty</em>';
    }
    
    if (type === 'json') {
        try {
            const obj = typeof value === 'string' ? JSON.parse(value) : value;
            const json = JSON.stringify(obj);
            if (json.length > 50) {
                return escapeHtml(json.substring(0, 50)) + '...';
            }
            return escapeHtml(json);
        } catch (e) {
            return escapeHtml(String(value));
        }
    }
    
    if (typeof value === 'string' && value.length > 50) {
        return escapeHtml(value.substring(0, 50)) + '...';
    }
    
    return escapeHtml(String(value));
}

// Save workflow variable definitions to localStorage
function saveWorkflowVariableDefinitions() {
    localStorage.setItem('workflowVariableDefinitions', JSON.stringify(workflowVariableDefinitions));
}

// Load workflow variable definitions from localStorage
function loadWorkflowVariableDefinitions(workflow) {
    // Set workflow variables from the loaded workflow
    const variablesLoaded = setWorkflowVariablesFromJson(workflow);

    // Only load from localStorage if no variables were found in the workflow JSON
    if (!variablesLoaded) {
        const saved = localStorage.getItem('workflowVariableDefinitions');
        if (saved) {
            try {
                workflowVariableDefinitions = JSON.parse(saved);
            } catch (e) {
                console.error('Error loading workflow variable definitions:', e);
                workflowVariableDefinitions = {};
            }
        } else {
            workflowVariableDefinitions = {};
        }
    }
}


// Function to extract variable name from ${variable} syntax
function extractVariableName(text) {
    // Check if this is a simple variable reference like ${varName}
    const match = text.match(/^\${([a-zA-Z0-9_]+)}$/);
    if (match) {
        return match[1]; // Return just the name inside the brackets
    }
    
    // If not a simple variable reference, return the original text
    return text;
}

// Replace variable references in a string with their values
function replaceVariableReferences(str, variables) {
    // console.log(`*** Replacing variable reference ***`);
    // console.log(`*** str/config.sourcePath: ${str}`);
    // console.log(`*** variables: ${formatJsonOutput(variables)}`);
    // console.log(`*** typeof input str???: ${typeof str}`);
    // if (isDebugMode) {
    //     addDebugLogEntry(`replaceVariableReferences->input str: ${str}`, 'info');
    //     addDebugLogEntry(`replaceVariableReferences->typeof input str: ${typeof     str}`, 'info');
    // }

    if (typeof str !== 'string') return str;
    
    // Pattern to match ${varName} or $varName
    const toReplacementString = (value) => {
        if (value === undefined) return undefined; // signal to keep original match
        if (value === null) return '';
        if (typeof value === 'object') {
            try {
                return JSON.stringify(value);
            } catch (err) {
                try {
                    // Fallback safe stringify to handle circular refs
                    const cache = new Set();
                    return JSON.stringify(value, (key, val) => {
                        if (typeof val === 'object' && val !== null) {
                            if (cache.has(val)) return '[Circular]';
                            cache.add(val);
                        }
                        return val;
                    });
                } catch (err2) {
                    return String(value);
                }
            }
        }
        return String(value);
    };

    return str
        .replace(/\${([a-zA-Z][a-zA-Z0-9_]*)}/g, (match, varName) => {
            const value = toReplacementString(variables[varName]);
            return value === undefined ? match : value;
        })
        .replace(/\$([a-zA-Z][a-zA-Z0-9_]*)/g, (match, varName) => {
            const value = toReplacementString(variables[varName]);
            return value === undefined ? match : value;
        });
}

// Update node actions to handle workflow variables
// Enhance all node configuration templates to include variable selector
// TODO fix issue causing multiple input-group tags that with no end tag
function enhanceConfigTemplates() {
    // For each configuration template type
    Object.keys(nodeConfigTemplates).forEach(type => {
        const template = nodeConfigTemplates[type];

        // Skip templates that already have integrated variable selector buttons
        if (type === 'Document' || type === 'Database' || type === 'Folder Selector' || type === 'AI Action' || type === 'Execute Application' || type === 'End Loop' || type === 'File' || type === 'Conditional') {
            return;
        }
        
        // Add workflow variables button to each text input and textarea
        // that's not already in an input group with a variable selector
        const enhancedTemplate = template.template.replace(
            /<input type="text"([^>]*)>/g, 
            (match, attributes) => {
                // Check if this input is already wrapped in an input-group with a variable button
                // This is a simplistic check, but should work for most cases
                if (match.includes('input-group') || match.includes('showVariableSelector')) {
                    return match;
                }
                return '<div class="input-group">'+match+'<button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)"><i class="bi bi-braces"></i></button></div>';
            }
        ).replace(
            /<textarea([^>]*)>/g,
            (match, attributes) => {
                if (match.includes('input-group') || match.includes('showVariableSelector') || match.includes('data-no-enhance')) {
                    return match;
                }
                return '<div class="input-group">'+match+'<button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)"><i class="bi bi-braces"></i></button></div>';
            }
        );
        
        // Update the template
        nodeConfigTemplates[type].template = enhancedTemplate;
    });
}

// Call this when the document is loaded
// document.addEventListener('DOMContentLoaded', function() {
//     // Enhance config templates with variable selectors
//     enhanceConfigTemplates();
// });

// Show a variable selector dropdown - improved version
function showVariableSelector(button) {
    // Find the related input or textarea
    const inputGroup = button.closest('.input-group');
    const input = inputGroup.querySelector('input') || inputGroup.querySelector('textarea');
    if (!input) return;
    
    // Remove any existing dropdowns
    const existingDropdown = document.querySelector('.variables-dropdown');
    if (existingDropdown) {
        existingDropdown.remove();
    }
    
    // Create dropdown menu with proper z-index and positioning
    const dropdown = document.createElement('div');
    dropdown.className = 'dropdown-menu variables-dropdown p-2';
    dropdown.style.display = 'block';
    dropdown.style.position = 'absolute';
    dropdown.style.zIndex = '9999'; // Higher z-index to appear above modal
    dropdown.style.maxHeight = '300px';
    dropdown.style.overflow = 'auto';
    dropdown.style.minWidth = '200px';
    
    // Add header
    const header = document.createElement('h6');
    header.className = 'dropdown-header';
    header.textContent = 'Insert Variable';
    dropdown.appendChild(header);
    
    // Check if there are variables
    const variables = Object.keys(workflowVariableDefinitions);
    if (variables.length === 0) {
        const item = document.createElement('div');
        item.className = 'dropdown-item text-muted';
        item.textContent = 'No variables defined';
        dropdown.appendChild(item);
    } else {
        // Add each variable as an option
        variables.forEach(name => {
            const item = document.createElement('button');
            item.type = 'button'; // Explicit button type
            item.className = 'dropdown-item';
            item.textContent = name;
            item.onclick = function(e) {
                e.preventDefault();
                e.stopPropagation();
                
                // Insert variable reference at cursor position or append
                const cursorPos = input.selectionStart;
                const currentValue = input.value;
                const newValue = 
                    currentValue.substring(0, cursorPos) + 
                    '${' + name + '}' + 
                    currentValue.substring(cursorPos);
                
                input.value = newValue;
                dropdown.remove();
                
                // Set focus back to input
                setTimeout(() => {
                    input.focus();
                    input.selectionStart = cursorPos + name.length + 3; // +3 for ${} chars
                    input.selectionEnd = cursorPos + name.length + 3;
                }, 10);
            };
            dropdown.appendChild(item);
        });
    }
    
    // Add a divider
    const divider = document.createElement('div');
    divider.className = 'dropdown-divider';
    dropdown.appendChild(divider);
    
    // Add "Manage Variables" option
    const manageItem = document.createElement('button');
    manageItem.type = 'button'; // Explicit button type
    manageItem.className = 'dropdown-item';
    manageItem.innerHTML = '<i class="bi bi-gear"></i> Manage Variables';
    manageItem.onclick = function(e) {
        e.preventDefault();
        e.stopPropagation();
        dropdown.remove();
        openWorkflowVariables();
    };
    dropdown.appendChild(manageItem);
    
    // Position the dropdown relative to the button
    const rect = button.getBoundingClientRect();
    
    // Add to modal content (not document.body) to avoid z-index issues
    const modalContent = button.closest('.modal-content');
    if (modalContent) {
        modalContent.appendChild(dropdown);
        
        // Position relative to the modal content
        const modalRect = modalContent.getBoundingClientRect();
        dropdown.style.top = (rect.bottom - modalRect.top) + 'px';
        dropdown.style.left = (rect.left - modalRect.left) + 'px';
    } else {
        // Fallback to document.body if not in a modal
        document.body.appendChild(dropdown);
        dropdown.style.top = (rect.bottom + window.scrollY) + 'px';
        dropdown.style.left = (rect.left + window.scrollX) + 'px';
    }
    
    // Add click outside handler with proper cleanup
    const closeDropdown = function(e) {
        if (!dropdown.contains(e.target) && e.target !== button) {
            dropdown.remove();
            document.removeEventListener('mousedown', closeDropdown);
        }
    };
    
    // Use mousedown instead of click to ensure it fires before the modal's events
    setTimeout(() => {
        document.addEventListener('mousedown', closeDropdown);
    }, 10);
    
    // Prevent default behavior to avoid issues with modal focus
    button.addEventListener('click', function(e) {
        e.preventDefault();
        e.stopPropagation();
    }, { once: true });
}

// Modify workflow execution functions to handle variables
function updateWorkflowVariableValue(name, value) {
    if (!isDebugMode) return;
    
    const oldValue = workflowVariables[name];
    workflowVariables[name] = value;
    
    // Log the change
    addDebugLogEntry(`Variable "${name}" updated: ${formatJsonOutput(oldValue)} → ${formatJsonOutput(value)}`, 'info');
    
    // Update variables table
    updateVariablesTable();
}

// Add a special node action for setting workflow variables
async function executeSetVariableNode(config, prev_data = {}) {
    if (!config.variableName) {
        return { 
            success: false, 
            error: 'No variable name specified',
            data: prev_data
        };
    }
    
    try {
        // Get the value expression
        let valueExpression = config.valueExpression || '';
        
        // Check if value should come from previous output
        if (config.useOutputPath && config.outputPath) {
            // Use lodash's get to safely access nested properties
            valueExpression = _.get(prev_data, config.outputPath, valueExpression);
        }
        
        // Replace variable references
        valueExpression = replaceVariableReferences(valueExpression, workflowVariables);
        
        // Parse the expression if needed
        let value = valueExpression;
        
        // Try to evaluate as JavaScript if it's a complex expression
        if (config.evaluateAsExpression) {
            try {
                // Create a safe evaluation context with access to variables and prev_data
                const evalContext = {
                    ...workflowVariables,
                    _prevData: prev_data
                };
                
                // Construct a function to evaluate in context
                const evalFn = new Function('vars', 'prevData', `
                    with (vars) {
                        return ${valueExpression};
                    }
                `);
                
                value = evalFn(evalContext, prev_data);
            } catch (e) {
                console.error('Expression evaluation error:', e);
                // Fall back to string value
            }
        }
        
        // Update the variable
        workflowVariables[config.variableName] = value;
        
        if (isDebugMode) {
            addDebugLogEntry(`Set variable "${config.variableName}" to: ${formatJsonOutput(value)}`, 'info');
        }
        
        return {
            success: true,
            data: {
                ...prev_data,
                variableSet: config.variableName,
                variableValue: value
            }
        };
    } catch (error) {
        return {
            success: false,
            error: `Error setting variable: ${error.message}`,
            data: prev_data
        };
    }
}

// Add a "Set Variable" node type to the nodeConfigTemplates
nodeConfigTemplates['Set Variable'] = {
    template: `
        <div class="mb-3">
            <label class="form-label">Variable Name</label>
            <div class="input-group">
                <input type="text" class="form-control" name="variableName" list="variableNameList" 
                       placeholder="Select or type variable name...">
                <datalist id="variableNameList">
                    <!-- Variables will be populated here -->
                </datalist>
            </div>
            <small class="form-text text-muted">
                Select from defined variables or type a runtime variable name (e.g., loop variables, output variables).
            </small>
        </div>
        <div class="mb-3">
            <label class="form-label">Value Source</label>
            <div class="form-check">
                <input class="form-check-input" type="radio" name="valueSource" id="valueSource-direct" value="direct" checked
                       onchange="document.getElementById('direct-value-group').style.display=this.checked?'block':'none';
                                document.getElementById('output-path-group').style.display=!this.checked?'block':'none';">
                <label class="form-check-label" for="valueSource-direct">
                    Direct Value
                </label>
            </div>
            <div class="form-check">
                <input class="form-check-input" type="radio" name="valueSource" id="valueSource-output" value="output"
                       onchange="document.getElementById('direct-value-group').style.display=!this.checked?'block':'none';
                                document.getElementById('output-path-group').style.display=this.checked?'block':'none';">
                <label class="form-check-label" for="valueSource-output">
                    From Previous Step Output
                </label>
            </div>
        </div>
        <div id="direct-value-group" class="mb-3">
            <label class="form-label">Value Expression</label>
            <div class="input-group">
                <textarea class="form-control" name="valueExpression" rows="3" placeholder="Value or expression"></textarea>
                <button type="button" class="btn btn-outline-secondary" onclick="showVariableSelector(this)">
                    <i class="bi bi-braces"></i>
                </button>
            </div>
            <div class="form-check mt-2">
                <input class="form-check-input" type="checkbox" name="evaluateAsExpression" id="evaluateAsExpression">
                <label class="form-check-label" for="evaluateAsExpression">
                    Evaluate as Expression
                </label>
            </div>
            <small class="form-text text-muted">
                You can access existing variables using $\{varName\} syntax. If "Evaluate as Expression" is checked,
                you can use Python expressions like $\{varA\} + $\{varB\} or Math functions.
            </small>
        </div>
        <div id="output-path-group" class="mb-3" style="display: none;">
            <label class="form-label">Output Path</label>
            <input type="text" class="form-control" name="outputPath" placeholder="e.g., data.results[0].value">
            <small class="form-text text-muted">
                Specify the path to extract from previous node output. Use dot notation for nested properties.
            </small>
        </div>
    `,
    defaultConfig: {
        variableName: '',
        valueSource: 'direct',
        valueExpression: '',
        outputPath: '',
        evaluateAsExpression: false
    }
};

// Enhanced function to populate variable dropdown without unnecessary reloading
function populateVariableDropdown() {
    const varInput = document.querySelector('input[name="variableName"]');
    const varDatalist = document.getElementById('variableNameList');
    if (!varInput || !varDatalist) return;
    
    // Save the current value to restore it later
    const currentValue = varInput.value;
    
    // Get all variable names
    const variableNames = Object.keys(workflowVariableDefinitions);
    
    // Clear existing options
    varDatalist.innerHTML = '';
    
    // Add each variable as an option
    variableNames.forEach(name => {
        const option = document.createElement('option');
        option.value = name;
        varDatalist.appendChild(option);
    });
    
    // Restore the value
    if (currentValue) {
        varInput.value = currentValue;
    }
}


// Modify the configureNode function to populate variable dropdown
const originalConfigureNode = configureNode; // Pass previous function to 'original', then apply inside the new function while rewriting configureNode
configureNode = function() {
    // Call original function
    originalConfigureNode.apply(this, arguments);
    
    // After modal is shown, populate the variable dropdown
    document.getElementById('nodeConfigModal').addEventListener('shown.bs.modal', function() {

        // TODO: note this might cause issues when loading values in dropdowns b/c it seems to reload values and undo the setting of the dropdown to the saved value
        populateVariableDropdown();
        
        // Set up value source radio change handler
        const directRadio = document.getElementById('valueSource-direct');
        const outputRadio = document.getElementById('valueSource-output');
        
        if (directRadio && outputRadio) {
            const directValueGroup = document.getElementById('direct-value-group');
            const outputPathGroup = document.getElementById('output-path-group');
            
            // Set initial visibility based on saved config
            if (configuredNode) {
                const config = nodeConfigs.get(configuredNode.id) || {};
                if (config.valueSource === 'output') {
                    outputRadio.checked = true;
                    if (directValueGroup) directValueGroup.style.display = 'none';
                    if (outputPathGroup) outputPathGroup.style.display = 'block';
                } else {
                    directRadio.checked = true;
                    if (directValueGroup) directValueGroup.style.display = 'block';
                    if (outputPathGroup) outputPathGroup.style.display = 'none';
                }
            }
        }
    }, { once: true });
};

// Extend configureNode to load document types when configuring a Document node
const originalConfigureNode2 = configureNode || (() => {});
configureNode = function() {
    // Call original function
    originalConfigureNode2.apply(this, arguments);
    
    // Check if this is a Document node
    if (configuredNode && configuredNode.getAttribute('data-type') === 'Document') {
        // Add event listener for when the modal is shown
        document.getElementById('nodeConfigModal').addEventListener('shown.bs.modal', function() {
            // Initialize toggles
            toggleDocumentFields();
            
            // Load document types
            loadDocumentTypes();
        }, { once: true });
    }
};

// Extend configureNode to load document types when configuring a File node
const originalConfigureNode3 = configureNode || (() => {});
configureNode = function() {
    // Call original function
    originalConfigureNode3.apply(this, arguments);

    if (configuredNode && configuredNode.getAttribute('data-type') === 'File') {
        // Add this to your nodeConfigModal shown event handler
        document.getElementById('nodeConfigModal').addEventListener('shown.bs.modal', function() {
            // Initialize field visibility based on current selections
            const operationSelect = document.getElementById('file-operation-select');
            if (operationSelect) {
                toggleFileOperationFields();
                
                const contentSourceSelect = document.getElementById('file-content-source');
                if (contentSourceSelect) {
                    toggleFileContentSource();
                }
            }
        }, { passive: true });
    }
};

// Add "Set Variable" to executeNodeAction function
const originalExecuteNodeAction = executeNodeAction;
executeNodeAction = async function(node, prev_data = {}) {
    const type = node.getAttribute('data-type');
    const config = nodeConfigs.get(node.id) || {};

    // Save the original config before modifying it
    const originalConfig = JSON.parse(JSON.stringify(config));
    
    console.log(`==>> executeNodeAction->type->${type}`);
    if (type === 'Set Variable') {
        return await executeSetVariableNode(config, prev_data);
    }
    
    // TODO: This causes issues for output variables b/c it attempts to set them to their default value, which makes no sense for output
    // Process variables in config for all node types
    console.log(`Replacing variables in config:==>> executeNodeAction->config->${formatJsonOutput(config)}`);
    const processedConfig = {};
    Object.entries(config).forEach(([key, value]) => {
        if (typeof value === 'string') {
            console.log(`==>> type: string executeNodeAction->replaceVariableReferences->${key}->${value}`);
            processedConfig[key] = replaceVariableReferences(value, workflowVariables);
            console.log(`==>> type: string executeNodeAction->replaceVariableReferences->${key}->${processedConfig[key]}`);
        } else {
            console.log(`==>> type: ${typeof value} executeNodeAction->not a string->${key}->${value}`);
            processedConfig[key] = value;
        }
    });
    
    // Override config for this call only
    nodeConfigs.set(node.id, processedConfig);

    // Store the original config to restore it after node execution
    node._originalConfig = originalConfig;
    
    // Call original function
    const result = await originalExecuteNodeAction.call(this, node, prev_data);
    
    // For specific node types, save their output to variables if configured
    if (result.success && config.saveOutputToVariable && config.outputVariableName) {
        updateWorkflowVariableValue(config.outputVariableName, result.data);
    }

    // Restore original config
    nodeConfigs.set(node.id, node._originalConfig);
    delete node._originalConfig;
    console.log(`executeNodeAction: restored original configuration for ${node.id}`);

    return result;
};


// Update executeNodeAction to handle Document nodes
const originalExecuteNodeAction2 = executeNodeAction || (async (node, prev_data) => {
    throw new Error('Original executeNodeAction function not found');
});

executeNodeAction = async function(node, prev_data = {}) {
    const type = node.getAttribute('data-type');
    const config = nodeConfigs.get(node.id) || {};
    console.log(`===>>> executeNodeAction->config: ${formatJsonOutput(config)}`);
    // Handle document nodes
    if (type === 'Document') {
        console.log('=====>>>>> executeDocumentAction!');
        return await executeDocumentAction(config, prev_data);
    }
    
    // For other node types, call the original function
    return await originalExecuteNodeAction2.call(this, node, prev_data);
};

// Add to your initializeDebugPanel function or directly to the DOMContentLoaded event
function setupDebugPanelResizing() {
    const debugPanel = document.getElementById('debug-panel');
    if (!debugPanel) return;
    
    // Store the initial height when starting resize
    let initialHeight;
    let initialY;
    let isResizing = false;
    
    // Function to handle mouse down on the header (to start resizing)
    const startResize = function(e) {
        // Only handle resize if clicking in the header area but not on buttons
        if (e.target.closest('button')) return;
        
        // If clicking very near the top edge, treat as resize
        const rect = debugPanel.getBoundingClientRect();
        const distanceFromTop = e.clientY - rect.top;
        
        if (distanceFromTop < 10) {  // Within 10px of top edge
            isResizing = true;
            initialHeight = debugPanel.offsetHeight;
            initialY = e.clientY;
            e.preventDefault();
            
            // Add temporary transparent overlay to handle mouse events during resize
            const overlay = document.createElement('div');
            overlay.style.position = 'fixed';
            overlay.style.top = '0';
            overlay.style.left = '0';
            overlay.style.right = '0';
            overlay.style.bottom = '0';
            overlay.style.zIndex = '9999';
            overlay.style.cursor = 'ns-resize';
            document.body.appendChild(overlay);
            
            // Handle mouse move during resize
            const handleMouseMove = function(e) {
                if (isResizing) {
                    const newHeight = initialHeight - (e.clientY - initialY);
                    debugPanel.style.height = `${Math.max(40, Math.min(window.innerHeight * 0.8, newHeight))}px`;
                }
            };
            
            // Handle mouse up to end resize
            const handleMouseUp = function() {
                isResizing = false;
                document.body.removeChild(overlay);
                document.removeEventListener('mousemove', handleMouseMove);
                document.removeEventListener('mouseup', handleMouseUp);
            };
            
            document.addEventListener('mousemove', handleMouseMove);
            document.addEventListener('mouseup', handleMouseUp);
        }
    };
    
    // Add event listener to debug header
    const debugHeader = document.querySelector('.debug-header');
    if (debugHeader) {
        debugHeader.addEventListener('mousedown', startResize);
    }
}

function toggleFileOperationFields() {
    const operation = document.getElementById('file-operation-select')?.value || 'read';
    const contentSection = document.getElementById('file-content-section');
    const contentSectionInner = document.getElementById('file-content-section-inner');
    const outputSection = document.getElementById('file-output-section');
    const outputHelp = document.getElementById('file-output-help');
    const destinationSection = document.getElementById('file-destination-section');
    
    // Reset all sections to hidden first
    if (contentSection) contentSection.style.display = 'none';
    if (contentSectionInner) contentSectionInner.style.display = 'none';
    if (outputSection) outputSection.style.display = 'none';
    if (destinationSection) destinationSection.style.display = 'none';
    
    // Show/hide sections based on operation type
    switch(operation) {
        case 'read':
            // For read: only show output section (to store the file content)
            if (outputSection) outputSection.style.display = 'block';
            if (outputHelp) {
                outputHelp.textContent = 'This variable will contain the content read from the file.';
            }
            break;
            
        case 'write':
        case 'append':
            // For write/append: show content input sections
            if (contentSection) contentSection.style.display = 'block';
            if (contentSectionInner) contentSectionInner.style.display = 'block';
            if (outputSection) outputSection.style.display = 'block';
            if (outputHelp) {
                outputHelp.textContent = 'This variable will be set to true if the operation succeeds, false otherwise.';
            }
            // Initialize the content source visibility
            toggleFileContentSource();
            break;
            
        case 'check':
            // For check: only show output section (to store true/false)
            if (outputSection) outputSection.style.display = 'block';
            if (outputHelp) {
                outputHelp.textContent = 'This variable will be set to true if the file exists, false otherwise.';
            }
            break;
            
        case 'delete':
            // For delete: only show output section (to store success status)
            if (outputSection) outputSection.style.display = 'block';
            if (outputHelp) {
                outputHelp.textContent = 'This variable will be set to true if the file was deleted successfully.';
            }
            break;
            
        case 'copy':
        case 'move':
            // For copy/move: show output section and add destination path field if needed
            if (outputSection) outputSection.style.display = 'block';
            if (outputHelp) {
                outputHelp.textContent = `This variable will be set to true if the ${operation} operation succeeds.`;
            }
            if (destinationSection) destinationSection.style.display = 'block';
            break;
            
        default:
            // Hide everything for unknown operations
            break;
    }
}


// Function to toggle between content source options
function toggleFileContentSource() {
    const source = document.getElementById('file-content-source')?.value;
    const directContent = document.getElementById('file-direct-content');
    const variableContent = document.getElementById('file-variable-content');
    const previousContent = document.getElementById('file-previous-content');
    
    if (directContent) directContent.style.display = source === 'direct' ? 'block' : 'none';
    if (variableContent) variableContent.style.display = source === 'variable' ? 'block' : 'none';
    if (previousContent) previousContent.style.display = source === 'previous' ? 'block' : 'none';
}


// Helper function to get nested value from object using dot notation
function getNestedValue(obj, path) {
    if (!path) return obj;
    
    const keys = path.split('.');
    let result = obj;
    
    for (const key of keys) {
        if (result === null || result === undefined) return undefined;
        
        // Handle array indices in the path (e.g., "data.results[0].value")
        const match = key.match(/^([^\[]+)(?:\[(\d+)\])?$/);
        if (match) {
            const [, propName, arrayIndex] = match;
            result = result[propName];
            
            if (arrayIndex !== undefined && Array.isArray(result)) {
                result = result[parseInt(arrayIndex, 10)];
            }
        } else {
            result = result[key];
        }
    }
    
    return result;
}

// Function to toggle database operation fields based on selected operation
// TODO fix db-query-group not being shown
function toggleDbOperationFields() {
    const operation = document.getElementById('db-operation-select')?.value || 'query';
    const queryGroup = document.getElementById('db-query-group');
    const procedureGroup = document.getElementById('db-procedure-group');
    const tableGroup = document.getElementById('db-table-group');
    const columnsGroup = document.getElementById('db-columns-group');
    const whereGroup = document.getElementById('db-where-group');
    const dataGroup = document.getElementById('db-data-group');
    
    // Hide all groups first
    if (queryGroup) queryGroup.style.display = 'none';
    if (procedureGroup) procedureGroup.style.display = 'none';
    if (tableGroup) tableGroup.style.display = 'none';
    
    // Show relevant groups based on operation
    switch (operation) {
        case 'query':
            if (queryGroup) queryGroup.style.display = 'block';
            break;
            
        case 'procedure':
            if (procedureGroup) procedureGroup.style.display = 'block';
            break;
            
        case 'select':
            if (tableGroup) tableGroup.style.display = 'block';
            if (columnsGroup) columnsGroup.style.display = 'block';
            if (whereGroup) whereGroup.style.display = 'block';
            if (dataGroup) dataGroup.style.display = 'none';
            break;
            
        case 'insert':
            if (tableGroup) tableGroup.style.display = 'block';
            if (columnsGroup) columnsGroup.style.display = 'none';
            if (whereGroup) whereGroup.style.display = 'none';
            if (dataGroup) dataGroup.style.display = 'block';
            break;
            
        case 'update':
            if (tableGroup) tableGroup.style.display = 'block';
            if (columnsGroup) columnsGroup.style.display = 'none';
            if (whereGroup) whereGroup.style.display = 'block';
            if (dataGroup) dataGroup.style.display = 'block';
            break;
            
        case 'delete':
            if (tableGroup) tableGroup.style.display = 'block';
            if (columnsGroup) columnsGroup.style.display = 'none';
            if (whereGroup) whereGroup.style.display = 'block';
            if (dataGroup) dataGroup.style.display = 'none';
            break;
    }
}

// Function to toggle between data source options for database operations
function toggleDbDataSource() {
    const source = document.getElementById('db-data-source')?.value;
    const directData = document.getElementById('db-direct-data');
    const variableData = document.getElementById('db-variable-data');
    const previousData = document.getElementById('db-previous-data');
    
    if (directData) directData.style.display = source === 'direct' ? 'block' : 'none';
    if (variableData) variableData.style.display = source === 'variable' ? 'block' : 'none';
    if (previousData) previousData.style.display = source === 'previous' ? 'block' : 'none';
}

// Add this function to toggle the pattern input visibility
function toggleFileSelectionOptions(selectElement) {
    const patternDiv = document.getElementById('pattern-option');
    if (selectElement.value === 'pattern') {
        patternDiv.style.display = 'block';
    } else {
        patternDiv.style.display = 'none';
    }
}

// Update the existing executeNodeAction function to include the new node type
async function executeFolderSelectorAction(config, node, prev_data = {}) {
    console.log('Executing folder selector action:', config);
    
    try {
        const folderPath = replaceVariableReferences(config.folderPath, workflowVariables);
        
        if (!folderPath) {
            return { 
                success: false, 
                error: 'Folder path is empty or undefined',
                data: prev_data
            };
        }
        
        const selectionMode = config.selectionMode || 'first';
        let filePattern = config.filePattern || '*.*';
        
        // Replace variables in the file pattern
        filePattern = replaceVariableReferences(filePattern, workflowVariables);
        
        // Call the API to list files in the folder
        const response = await fetch('/folder/list_files', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                folderPath: folderPath,
                filePattern: filePattern,
                selectionMode: selectionMode
            })
        });
        
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.message || 'Failed to list files in folder');
        }
        
        const result = await response.json();

        console.log(`folder/list_files result: ${formatJsonOutput(result)}`);
        
        if (result.status !== 'success') {
            throw new Error(result.message || 'Failed to list files in folder');
        }
        
        const selectedFile = result.selectedFile;

        console.log(`folder/list_files selectedFile: ${formatJsonOutput(selectedFile)}`);
        
        // If no files found, handle based on configuration
        if (!selectedFile) {
            if (config.failIfEmpty) {
                return {
                    success: false,
                    error: `No files found in folder: ${folderPath}`,
                    data: {
                        ...prev_data,
                        folderPath: folderPath,
                        filesFound: false
                    }
                };
            } else {
                // Set output variable to empty string
                if (config.outputVariable) {
                    workflowVariables[config.outputVariable] = '';
                }
                
                return {
                    success: true,
                    data: {
                        ...prev_data,
                        folderPath: folderPath,
                        filesFound: false,
                        selectedFile: null
                    }
                };
            }
        }
        
        // Set output variable if specified
        // TODO: Copy this function to the AI Action node and modify it to set the variable in the workflowVariables object
        console.log(`Output variable: ${node._originalConfig.outputVariable}`);
        console.log(`Variable name: ${extractVariableName(node._originalConfig.outputVariable)}`);
        if (node._originalConfig.outputVariable) {
            const folderOutputVariable = extractVariableName(node._originalConfig.outputVariable);
            workflowVariables[folderOutputVariable] = selectedFile;
            
            if (isDebugMode) {
                addDebugLogEntry(`Set variable "${folderOutputVariable}" to: ${selectedFile}`, 'info');
                updateVariablesTable();
            }
            
        }
        
        return {
            success: true,
            data: {
                ...prev_data,
                folderPath: folderPath,
                filesFound: true,
                selectedFile: selectedFile,
                allFiles: result.allFiles
            }
        };
        
    } catch (error) {
        console.error('Error executing folder selector:', error);
        return {
            success: false,
            error: error.message,
            data: prev_data
        };
    }
}

// ********** DEBUG ONLY ********** //
// Functions for workflow execution
// Modify the existing startWorkflow function to integrate with debug mode
async function startWorkflow_Debug() {
    if (!startNode) {
        //alert('Please set a start node for the workflow');
        showToast(`Please set a start node for the workflow`, 'warning');
        return;
    }
    console.log(`Starting workflow...`);
    // Reset workflow variables for new run
    workflowVariables = {};
    
    // Initialize workflow variables with default values from definitions
    Object.entries(workflowVariableDefinitions).forEach(([name, def]) => {
        try {
            // Convert value to correct type
            let value = def.defaultValue;
            
            if (def.type === 'number') {
                value = Number(value);
            } else if (def.type === 'boolean') {
                value = value === 'true';
            } else if (def.type === 'json') {
                value = value ? JSON.parse(value) : {};
            }
            
            workflowVariables[name] = value;
        } catch (e) {
            console.error(`Error setting default value for variable ${name}:`, e);
            workflowVariables[name] = def.defaultValue;
        }
    });

    const runBtn = document.getElementById('runWorkflowBtn');
    const stopBtn = document.getElementById('stopWorkflowBtn');
    const statusDiv = document.getElementById('workflowStatus');

    runBtn.classList.remove('active');
    stopBtn.classList.add('active');

    runBtn.style.display = 'none';
    stopBtn.style.display = 'inline-block';
    statusDiv.style.display = 'inline-block';
    
    isWorkflowRunning = true;
    
    // If debug mode is enabled, clear previous debug data
    if (isDebugMode) {
        clearDebugLogs();
        const debugStatus = document.getElementById('workflow-debug-status');
        if (debugStatus) {
            debugStatus.textContent = 'Running';
            debugStatus.parentElement.classList.remove('bg-primary', 'bg-secondary');
            debugStatus.parentElement.classList.add('bg-running');
        }
        
        addDebugLogEntry('Workflow execution started', 'info');
        addDebugLogEntry(`Starting from node: ${startNode.querySelector('.node-content').textContent.trim()}`, 'info');
        
        // Show initial variables
        updateVariablesTable();
    }
    
    // Reset all nodes' visual states
    document.querySelectorAll('.workflow-node').forEach(node => {
        node.classList.remove('executing', 'completed', 'error', 'debug-current', 'debug-passed', 'debug-error');
    });

    try {
        await executeWorkflowNode(startNode);
        //showStatus('Workflow completed successfully', 'success');
        
        if (isDebugMode) {
            addDebugLogEntry('Workflow execution completed successfully', 'success');
            const debugStatus = document.getElementById('workflow-debug-status');
            if (debugStatus) {
                debugStatus.textContent = 'Completed';
                debugStatus.parentElement.classList.remove('bg-running');
                debugStatus.parentElement.classList.add('bg-success');
            }
        }
    } catch (error) {
        showStatus('Workflow failed: ' + error.message, 'error');
        
        if (isDebugMode) {
            addDebugLogEntry(`Workflow execution failed: ${error.message}`, 'error');
            const debugStatus = document.getElementById('workflow-debug-status');
            if (debugStatus) {
                debugStatus.textContent = 'Failed';
                debugStatus.parentElement.classList.remove('bg-running');
                debugStatus.parentElement.classList.add('bg-error');
            }
        }
    } finally {
        if (isWorkflowRunning) { // Only reset if not manually stopped
            stopWorkflow();
        }
    }
}


// ********** END DEBUG ONLY ********** //

// Function to save workflow and return its ID
async function saveWorkflowBeforeExecution() {
    try {
        // Generate a filename if none exists yet
        console.log(`Current workflow name: ${currentWorkflowName}`);
        let workflowName = null;
        if (currentWorkflowName) workflowName = currentWorkflowName;
        if (!workflowName) {
            workflowName = prompt('Enter a name for this workflow:');
            // Set global workflow name
            currentWorkflowName = workflowName;
            // Update the display
            updateCurrentWorkflowDisplay();
            console.log(`New workflow name: ${workflowName}`);
        }
        if (!workflowName) return null;
        
        // Save nodes
        const nodes = [];
        const connections = [];
        document.querySelectorAll('.workflow-node').forEach(node => {
            nodes.push({
                id: node.id,
                type: node.getAttribute('data-type'),
                label: node.querySelector('.node-content').textContent.trim(),
                position: {
                    left: node.style.left,
                    top: node.style.top
                },
                config: nodeConfigs.get(node.id) || {},
                isStart: node === startNode
            });
        });
        
        // Save connections
        jsPlumbInstance.getAllConnections().forEach(conn => {
                        // Get the actual endpoints (anchors)
                        const sourceEndpoint = conn.endpoints[0];
                        const targetEndpoint = conn.endpoints[1];
                        
                        // Get anchor information
                        const sourceAnchor = sourceEndpoint.anchor.type || "Right";
                        const targetAnchor = targetEndpoint.anchor.type || "Left";

            connections.push({
                source: conn.source.id,
                target: conn.target.id,
                type: conn.getData().type || 'pass',
                // Save the anchor information
                sourceAnchor: sourceAnchor,
                targetAnchor: targetAnchor
            });
        });
        
        // Add variable definitions to the workflow data
        const workflowData = { 
            nodes, 
            connections, 
            variables: workflowVariableDefinitions
        };

        console.log('Saving workflow data: ', formatJsonOutput(workflowData));
        
        // Send to server
        const response = await fetch('/save/workflow', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                filename: workflowName,
                workflow: workflowData
            })
        });
        
        if (!response.ok) {
            showFeedback(`Failed to save workflow - ${response.status}`, 'error');
            throw new Error(`Server error: ${response.status}`);
        }
        
        const result = await response.json();
        
        if (result.status !== 'success') {
            showFeedback(`Failed to save workflow - ${result.message}`, 'error');
            throw new Error(result.message || 'Unknown error');
        }
        // Load initial data
        loadCategories();
        populateWorkflowsDropdown();

        // TRAINING CAPTURE: This call is fire-and-forget - won't block or show errors to user
        //console.log("Finalizing training capture...");
        //await finalizeTrainingCapture(workflowData, 'general');

        showFeedback('Workflow saved successfully!', 'success');
        return result.workflow_id;
    } catch (error) {
        console.error('Error saving workflow:', error);
        throw error;
    }
}

// Function to poll for status updates
let statusPollingInterval = null;

function startStatusPolling(executionId) {
    // Clear any existing interval
    if (statusPollingInterval) {
        clearInterval(statusPollingInterval);
    }
    
    // Define the polling function
    const pollStatus = async () => {
        try {
            const response = await fetch(`/api/workflow/executions/${executionId}`);
            const data = await response.json();
            
            // Update the status display
            const statusDiv = document.getElementById('workflowStatus');
            statusDiv.textContent = data.status;
            
            // Update class based on status
            statusDiv.className = 'workflow-status';
            switch(data.status.toLowerCase()) {
                case 'running':
                    statusDiv.classList.add('bg-running');
                    break;
                case 'paused':
                    statusDiv.classList.add('bg-warning');
                    break;
                case 'completed':
                    statusDiv.classList.add('bg-success');
                    stopStatusPolling();
                    stopWorkflow(true); // Reset UI but don't cancel execution
                    break;
                case 'failed':
                    statusDiv.classList.add('bg-error');
                    stopStatusPolling();
                    stopWorkflow(true);
                    break;
                case 'cancelled':
                    statusDiv.classList.add('bg-secondary');
                    stopStatusPolling();
                    stopWorkflow(true);
                    break;
                default:
                    statusDiv.classList.add('bg-secondary');
            }
            
            // Visualize current step in the workflow
            visualizeWorkflowExecution(data);
            
        } catch (error) {
            console.error('Error polling workflow status:', error);
            // Don't stop polling on temporary errors
        }
    };
    
    // Start polling (every 2 seconds)
    statusPollingInterval = setInterval(pollStatus, 2000);
    
    // Initial poll
    pollStatus();
}

function stopStatusPolling() {
    if (statusPollingInterval) {
        clearInterval(statusPollingInterval);
        statusPollingInterval = null;
    }
}

// For handling existing connections:
function bindContextMenuToAllConnections() {
    jsPlumbInstance.getAllConnections().forEach(connection => {
        connection.canvas.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            e.stopPropagation();
            selectedConnection = connection;
            selectedNode = null;
            
            const arrowMenu = document.getElementById('arrow-context-menu');
            arrowMenu.style.display = 'block';
            arrowMenu.style.left = `${e.pageX}px`;
            arrowMenu.style.top = `${e.pageY}px`;
        });
    });
}

// Call this after loading a workflow
function enhanceLoadWorkflow() {
    const originalLoadWorkflow = loadWorkflow;
    loadWorkflow = function(workflow) {
        originalLoadWorkflow.apply(this, arguments);
        bindContextMenuToAllConnections();
    };
}

// Function to create a new workflow
function createNewWorkflow(showConfirm=true) {
    // Confirm with the user if there's an existing workflow open
    if (currentWorkflowName && showConfirm) {
        if (!confirm('Are you sure you want to create a new workflow? Any unsaved changes will be lost.')) {
            return;
        }
    }
    
    // Clear the canvas and reset all state
    clearWorkflow();  // This now includes re-establishing event bindings

    // Reset current workflow indicators
    currentWorkflowName = null;
    updateCurrentWorkflowDisplay();
    
    // Clear variables
    workflowVariableDefinitions = {};
    workflowVariables = {};

    clearDebugPanelData();
    
    // Reset node counter
    nodeCounter = 0;

    // Reset AI Builder session to get fresh backend session
    console.log("Resetting AI Builder session...");
    if (window.workflowBuilder) {
        console.log("Calling resetSession...");
        window.workflowBuilder.resetSession();
    }
    
    // Show a toast notification
    if (showConfirm) {
        showToast('New workflow created', 'success');
    }
}

// Function to clear the current workflow
function clearWorkflow() {
    const canvas = document.getElementById('workflow-canvas');
    if (canvas) {
        canvas.innerHTML = '';
        jsPlumbInstance.reset();
        startNode = null;
        nodeConfigs.clear();
    }
}

// Function to update the current workflow display
function updateCurrentWorkflowDisplay() {
    const displayElement = document.getElementById('currentWorkflowDisplay');
    const nameElement = document.getElementById('currentWorkflowName');
    
    if (displayElement && nameElement) {
        if (currentWorkflowName) {
            nameElement.textContent = currentWorkflowName;
            displayElement.classList.remove('d-none');
            displayElement.classList.add('active');
        } else {
            nameElement.textContent = 'None';
            displayElement.classList.add('d-none');
            displayElement.classList.remove('active');
        }
    }
}

// Update the load workflow function to update the display
const originalLoadSelectedWorkflow = loadSelectedWorkflow;
loadSelectedWorkflow = async function() {
    try {
        await originalLoadSelectedWorkflow.apply(this, arguments);
        // The currentWorkflowName should be set in the original function
        updateCurrentWorkflowDisplay();
    } catch (error) {
        // If there's an error, make sure we clear the current workflow name
        console.error('Error loading workflow:', error);
        currentWorkflowName = null;
        updateCurrentWorkflowDisplay();
    }
};

// Update the save workflow function to update the display
const originalSaveWorkflow = saveWorkflow;
saveWorkflow = async function() {
    try {
        await originalSaveWorkflow.apply(this, arguments);
        // After saving, update the display
        updateCurrentWorkflowDisplay();
    } catch (error) {
        console.error('Error saving workflow:', error);
    }
};


// ============================================
// WORKFLOW EXPORT / IMPORT / COPY FUNCTIONS
// ============================================

/**
 * Get the current workflow state as a JSON object
 * @returns {Object} The workflow data object
 */
function getCurrentWorkflowState() {
    const nodes = [];
    const connections = [];
    
    // Collect all nodes
    document.querySelectorAll('.workflow-node').forEach(node => {
        nodes.push({
            id: node.id,
            type: node.getAttribute('data-type'),
            label: node.querySelector('.node-content').textContent.trim(),
            position: {
                left: node.style.left,
                top: node.style.top
            },
            config: nodeConfigs.get(node.id) || {},
            isStart: node === startNode
        });
    });
    
    // Collect all connections
    jsPlumbInstance.getAllConnections().forEach(conn => {
        const sourceEndpoint = conn.endpoints[0];
        const targetEndpoint = conn.endpoints[1];
        const sourceAnchor = sourceEndpoint.anchor.type || "Right";
        const targetAnchor = targetEndpoint.anchor.type || "Left";

        connections.push({
            source: conn.source.id,
            target: conn.target.id,
            type: conn.getData().type || 'pass',
            sourceAnchor: sourceAnchor,
            targetAnchor: targetAnchor
        });
    });
    
    return {
        nodes,
        connections,
        variables: workflowVariableDefinitions,
        metadata: {
            name: currentWorkflowName || 'Untitled Workflow',
            exportedAt: new Date().toISOString(),
            version: '1.0'
        }
    };
}

/**
 * Export the current workflow to a downloadable JSON file
 */
function exportWorkflow() {
    try {
        // Get workflow state
        const workflowData = getCurrentWorkflowState();
        
        // Check if there's anything to export
        if (workflowData.nodes.length === 0) {
            showToast('No workflow to export. Please create or load a workflow first.', 'warning');
            return;
        }
        
        // Create JSON string with pretty formatting
        const jsonString = JSON.stringify(workflowData, null, 2);
        
        // Create a blob and download link
        const blob = new Blob([jsonString], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        
        // Generate filename
        const workflowName = currentWorkflowName || 'workflow';
        const sanitizedName = workflowName.replace(/[^a-z0-9]/gi, '_').toLowerCase();
        const timestamp = new Date().toISOString().slice(0, 10);
        const filename = `${sanitizedName}_${timestamp}.json`;
        
        // Create download link and trigger download
        const downloadLink = document.createElement('a');
        downloadLink.href = url;
        downloadLink.download = filename;
        document.body.appendChild(downloadLink);
        downloadLink.click();
        document.body.removeChild(downloadLink);
        
        // Clean up the URL object
        URL.revokeObjectURL(url);
        
        showToast(`Workflow exported as "${filename}"`, 'success');
        console.log('Workflow exported successfully:', filename);
        
    } catch (error) {
        console.error('Error exporting workflow:', error);
        showToast('Error exporting workflow: ' + error.message, 'error');
    }
}

/**
 * Trigger the file import dialog
 */
function importWorkflow() {
    // Check if there's an existing workflow that might be lost
    const hasExistingWorkflow = document.querySelectorAll('.workflow-node').length > 0;
    
    if (hasExistingWorkflow) {
        if (!confirm('Importing a workflow will replace the current workflow. Any unsaved changes will be lost. Continue?')) {
            return;
        }
    }
    
    // Trigger the hidden file input
    const fileInput = document.getElementById('loadWorkflow');
    if (fileInput) {
        fileInput.click();
    } else {
        showToast('Import functionality not available', 'error');
    }
}

/**
 * Enhanced version of loadWorkflowFile that handles the imported file
 * This overrides the existing function to add better error handling and metadata support
 */
const originalLoadWorkflowFile = loadWorkflowFile;
loadWorkflowFile = function(input) {
    const file = input.files[0];
    if (!file) return;
    
    // Validate file type
    if (!file.name.endsWith('.json')) {
        showToast('Please select a JSON file', 'error');
        input.value = ''; // Reset the input
        return;
    }
    
    const reader = new FileReader();
    reader.onload = function(e) {
        try {
            const workflow = JSON.parse(e.target.result);
            
            // Validate workflow structure
            if (!workflow.nodes || !workflow.connections) {
                throw new Error('Invalid workflow file: missing nodes or connections');
            }
            
            // Load the workflow
            loadWorkflow(workflow);
            
            // Set variables if present
            if (workflow.variables) {
                setWorkflowVariablesFromJson(workflow);
            }
            
            // Set the workflow name from metadata or filename
            if (workflow.metadata && workflow.metadata.name) {
                currentWorkflowName = workflow.metadata.name;
            } else {
                // Use filename without extension
                currentWorkflowName = file.name.replace('.json', '');
            }
            
            // Mark as imported (not saved yet) by appending " (Imported)"
            currentWorkflowName = currentWorkflowName + ' (Imported)';
            updateCurrentWorkflowDisplay();
            
            showToast(`Workflow imported successfully! Save to keep changes.`, 'success');
            console.log('Workflow imported:', currentWorkflowName);
            
        } catch (error) {
            console.error('Error loading workflow file:', error);
            showToast(`Error importing workflow: ${error.message}`, 'error');
        }
    };
    
    reader.onerror = function() {
        showToast('Error reading file', 'error');
    };
    
    reader.readAsText(file);
    
    // Reset the input so the same file can be selected again
    input.value = '';
};

/**
 * Copy/duplicate a workflow with a new name
 * Can be called from the workflow manager or for the current workflow
 * @param {number|string} workflowId - Optional workflow ID to copy. If not provided, copies the current workflow.
 */
async function copyWorkflow(workflowId = null) {
    try {
        let workflowData;
        let originalName;
        
        if (workflowId) {
            // Copying from the workflow manager - fetch the workflow data
            const response = await fetch(`/get/workflow/${workflowId}`);
            if (!response.ok) {
                throw new Error('Failed to load workflow for copying');
            }
            
            const data = await response.json();
            const parsedData = typeof data === 'string' ? JSON.parse(data) : data;
            
            // Handle nested structure
            workflowData = parsedData.workflow_data || parsedData;
            originalName = parsedData.workflow_name || parsedData.name || 'Workflow';
            
        } else {
            // Copying the current workflow
            workflowData = getCurrentWorkflowState();
            originalName = currentWorkflowName || 'Workflow';
            
            if (workflowData.nodes.length === 0) {
                showToast('No workflow to copy. Please create or load a workflow first.', 'warning');
                return;
            }
        }
        
        // Prompt for new name
        const newName = prompt('Enter a name for the copied workflow:', `${originalName} (Copy)`);
        if (!newName || newName.trim() === '') {
            return; // User cancelled
        }
        
        // Remove metadata that shouldn't be copied
        if (workflowData.metadata) {
            delete workflowData.metadata;
        }
        
        // Save the copy to the server
        const response = await fetch('/save/workflow', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                filename: newName.trim(),
                workflow: workflowData
            })
        });
        
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.message || 'Failed to save workflow copy');
        }
        
        const result = await response.json();
        
        if (result.status !== 'success') {
            throw new Error(result.message || 'Unknown error');
        }
        
        // Refresh the workflows list
        await populateWorkflowsDropdown();
        
        // If the workflow manager is open, refresh it too
        const tableBody = document.getElementById('workflowTableBody');
        if (tableBody && tableBody.closest('.modal.show')) {
            await refreshWorkflowsList();
        }
        
        showToast(`Workflow copied as "${newName}"`, 'success');
        console.log('Workflow copied successfully:', newName);
        
        // Ask if user wants to open the copy
        if (confirm(`Workflow "${newName}" created successfully. Would you like to open it now?`)) {
            // Find and select the new workflow
            const select = document.getElementById('workflowSelect');
            if (select) {
                // Look for the option with matching text
                for (let option of select.options) {
                    if (option.text === newName.trim()) {
                        select.value = option.value;
                        await loadSelectedWorkflow();
                        break;
                    }
                }
            }
        }
        
        return result.workflow_id;
        
    } catch (error) {
        console.error('Error copying workflow:', error);
        showToast('Error copying workflow: ' + error.message, 'error');
        return null;
    }
}

/**
 * Copy the currently loaded workflow
 */
function copyCurrentWorkflow() {
    copyWorkflow(null);
}

/**
 * Export a specific workflow by its ID (used from workflow manager)
 * @param {number|string} workflowId - The ID of the workflow to export
 */
async function exportWorkflowById(workflowId) {
    try {
        // Fetch the workflow data
        const response = await fetch(`/get/workflow/${workflowId}`);
        if (!response.ok) {
            throw new Error('Failed to load workflow for export');
        }
        
        const data = await response.json();
        const parsedData = typeof data === 'string' ? JSON.parse(data) : data;
        
        // Handle nested structure
        const workflowData = parsedData.workflow_data || parsedData;
        const workflowName = parsedData.workflow_name || parsedData.name || 'workflow';
        
        // Add metadata
        workflowData.metadata = {
            name: workflowName,
            exportedAt: new Date().toISOString(),
            version: '1.0'
        };
        
        // Create JSON string with pretty formatting
        const jsonString = JSON.stringify(workflowData, null, 2);
        
        // Create a blob and download link
        const blob = new Blob([jsonString], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        
        // Generate filename
        const sanitizedName = workflowName.replace(/[^a-z0-9]/gi, '_').toLowerCase();
        const timestamp = new Date().toISOString().slice(0, 10);
        const filename = `${sanitizedName}_${timestamp}.json`;
        
        // Create download link and trigger download
        const downloadLink = document.createElement('a');
        downloadLink.href = url;
        downloadLink.download = filename;
        document.body.appendChild(downloadLink);
        downloadLink.click();
        document.body.removeChild(downloadLink);
        
        // Clean up the URL object
        URL.revokeObjectURL(url);
        
        showToast(`Workflow exported as "${filename}"`, 'success');
        console.log('Workflow exported successfully:', filename);
        
    } catch (error) {
        console.error('Error exporting workflow:', error);
        showToast('Error exporting workflow: ' + error.message, 'error');
    }
}

// ============================================
// END WORKFLOW EXPORT / IMPORT / COPY
// ============================================


// Function to open the category manager
function openCategoryManager() {
    refreshCategoriesList().then(() => {
        categoryManagerModal.show();
    });
}

// Function to refresh the categories list
async function refreshCategoriesList() {
    try {
        const response = await fetch('/get/workflow/categories');
        if (!response.ok) {
            throw new Error('Failed to load categories');
        }
        
        const data = await response.json();
        
        // Convert data to array if it's not already
        let categories = [];
        if (typeof data === 'string') {
            categories = JSON.parse(data);
        } else if (Array.isArray(data)) {
            categories = data;
        } else if (typeof data === 'object') {
            categories = Object.values(data);
        }
        
        populateCategoriesTable(categories);
    } catch (error) {
        console.error('Error refreshing categories:', error);
        showToast('Error refreshing categories list', 'error');
    }
}

// Function to populate the categories table
function populateCategoriesTable(categories) {
    const tbody = document.getElementById('categoriesTableBody');
    
    // Clear existing rows
    tbody.innerHTML = '';
    
    if (categories.length === 0) {
        const row = document.createElement('tr');
        row.innerHTML = '<td colspan="2" class="text-center">No categories found</td>';
        tbody.appendChild(row);
        return;
    }
    
    // Add each category as a row
    categories.forEach(category => {
        const id = category.id || category.ID;
        const name = category.name || category.NAME;
        
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${name}</td>
            <td>
                <div class="btn-group btn-group-sm">
                    <button class="btn btn-outline-primary" onclick="editCategory(${id}, '${name}')">
                        <i class="bi bi-pencil"></i>
                    </button>
                    <button class="btn btn-outline-danger" onclick="deleteCategory(${id})">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
            </td>
        `;
        
        tbody.appendChild(row);
    });
}

// Function to show the add category form
function showAddCategoryForm() {
    document.getElementById('categoryFormTitle').textContent = 'Add Category';
    document.getElementById('categoryName').value = '';
    document.getElementById('categoryId').value = '0';
    document.getElementById('categoryForm').style.display = 'block';
    document.getElementById('categoryName').focus();
}

// Function to show the edit category form
function editCategory(id, name) {
    document.getElementById('categoryFormTitle').textContent = 'Edit Category';
    document.getElementById('categoryName').value = name;
    document.getElementById('categoryId').value = id;
    document.getElementById('categoryForm').style.display = 'block';
    document.getElementById('categoryName').focus();
}

// Function to hide the category form
function hideCategoryForm() {
    document.getElementById('categoryForm').style.display = 'none';
}

// Function to save a category (add or update)
async function saveCategory() {
    const categoryId = document.getElementById('categoryId').value;
    const categoryName = document.getElementById('categoryName').value.trim();
    
    if (!categoryName) {
        showToast('Category name is required', 'warning');
        return;
    }
    
    try {
        const isUpdate = categoryId !== '0';
        const url = isUpdate ? `/update/workflow/category/${categoryId}` : '/add/workflow/category';
        
        const response = await fetch(url, {
            method: isUpdate ? 'PUT' : 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                name: categoryName
            })
        });
        
        if (!response.ok) {
            throw new Error('Failed to save category');
        }
        
        const result = await response.json();
        
        if (result.status === 'success') {
            showToast(`Category ${isUpdate ? 'updated' : 'added'} successfully`, 'success');
            hideCategoryForm();
            await refreshCategoriesList();
            
            // Also refresh the category filter dropdown
            await loadCategories();
        } else {
            throw new Error(result.message || 'Unknown error');
        }
    } catch (error) {
        console.error('Error saving category:', error);
        showToast(`Error saving category: ${error.message}`, 'error');
    }
}

// Function to delete a category
async function deleteCategory(id) {
    if (!confirm('Are you sure you want to delete this category? Workflows in this category will be set to "Uncategorized".')) {
        return;
    }
    
    try {
        const response = await fetch(`/delete/workflow/category/${id}`, {
            method: 'DELETE'
        });
        
        if (!response.ok) {
            throw new Error('Failed to delete category');
        }
        
        const result = await response.json();
        
        if (result.status === 'success') {
            showToast('Category deleted successfully', 'success');
            await refreshCategoriesList();
            
            // Also refresh the category filter dropdown
            await loadCategories();
        } else {
            throw new Error(result.message || 'Unknown error');
        }
    } catch (error) {
        console.error('Error deleting category:', error);
        showToast(`Error deleting category: ${error.message}`, 'error');
    }
}

function updateConditionFields(conditionType) {
    // Hide all conditional field groups
    document.getElementById('comparison-fields').style.display = 'none';
    document.getElementById('expression-field').style.display = 'none';
    document.getElementById('contains-fields').style.display = 'none';
    document.getElementById('exists-field').style.display = 'none';
    document.getElementById('empty-field').style.display = 'none';
    
    // Show the selected field group
    switch(conditionType) {
        case 'comparison':
            document.getElementById('comparison-fields').style.display = 'block';
            break;
        case 'expression':
            document.getElementById('expression-field').style.display = 'block';
            break;
        case 'contains':
            document.getElementById('contains-fields').style.display = 'block';
            break;
        case 'exists':
            document.getElementById('exists-field').style.display = 'block';
            // Populate variable dropdown
            const existsSelect = document.querySelector('select[name="existsVariable"]');
            if (existsSelect) {
                existsSelect.innerHTML = '<option value="">Select a variable...</option>';
                Object.keys(workflowVariableDefinitions).forEach(varName => {
                    const option = document.createElement('option');
                    option.value = varName;
                    option.textContent = varName;
                    existsSelect.appendChild(option);
                });
            }
            break;
        case 'empty':
            document.getElementById('empty-field').style.display = 'block';
            break;
    }
}

function updateAlertFields(alertType) {
    const emailFields = document.getElementById('email-specific-fields');
    if (emailFields) {
        emailFields.style.display = (alertType === 'email') ? 'block' : 'none';
    }
}

async function executeConditionalAction(config, prev_data = {}) {
    try {
        let conditionResult = false;
        
        // Replace variable references in the config
        const processedConfig = {};
        for (const [key, value] of Object.entries(config)) {
            if (typeof value === 'string') {
                processedConfig[key] = replaceVariableReferences(value, workflowVariables);
            } else {
                processedConfig[key] = value;
            }
        }
        
        // Evaluate based on condition type
        switch (processedConfig.conditionType) {
            case 'comparison':
                const leftVal = evaluateValue(processedConfig.leftValue);
                const rightVal = evaluateValue(processedConfig.rightValue);
                conditionResult = evaluateComparison(leftVal, processedConfig.operator, rightVal);
                break;
                
            case 'expression':
                conditionResult = evaluateExpression(processedConfig.expression);
                break;
                
            case 'contains':
                const text = String(evaluateValue(processedConfig.containsText) || '');
                const search = String(processedConfig.searchText || '');
                conditionResult = text.includes(search);
                break;
                
            case 'exists':
                conditionResult = processedConfig.existsVariable in workflowVariables;
                break;
                
            case 'empty':
                // Use the raw (unresolved) config value here — we need the variable
                // NAME, not its resolved value. The generic replaceVariableReferences
                // pass above would turn "${customerList}" into its contents, breaking
                // the lookup on workflowVariables.
                const rawEmptyVar = (config.emptyVariable || '').toString().trim();
                const varName = rawEmptyVar.replace(/^\$\{|\}$/g, '').trim();
                if (!varName) {
                    // Nothing configured — treat as empty.
                    conditionResult = true;
                } else {
                    const value = workflowVariables[varName];
                    conditionResult =
                        value === undefined ||
                        value === null ||
                        value === '' ||
                        (Array.isArray(value) && value.length === 0) ||
                        (typeof value === 'object' && !Array.isArray(value) &&
                            Object.keys(value).length === 0);
                }
                break;
        }
        
        if (isDebugMode) {
            addDebugLogEntry(`Conditional evaluation: ${conditionResult ? 'TRUE' : 'FALSE'}`, 'info');
            addDebugLogEntry(`Condition details: ${JSON.stringify(processedConfig)}`, 'debug');
        }
        
        return {
            success: conditionResult,  // This determines pass/fail path
            data: {
                ...prev_data,
                conditionResult: conditionResult,
                conditionType: config.conditionType
            }
        };
        
    } catch (error) {
        if (isDebugMode) {
            addDebugLogEntry(`Conditional error: ${error.message}`, 'error');
        }
        return {
            success: false,
            error: error.message,
            data: prev_data
        };
    }
}

// Helper function to evaluate a value (variable or literal)
function evaluateValue(value) {
    if (typeof value !== 'string') return value;
    
    // Try to parse as number
    const num = Number(value);
    if (!isNaN(num) && value.trim() !== '') {
        return num;
    }
    
    // Check for boolean
    if (value === 'true') return true;
    if (value === 'false') return false;
    if (value === 'null') return null;
    
    // Try to parse as JSON
    try {
        return JSON.parse(value);
    } catch (e) {
        // Return as string
        return value;
    }
}

// Helper function to evaluate comparisons
function evaluateComparison(left, operator, right) {
    switch (operator) {
        case '==': return left == right;
        case '!=': return left != right;
        case '>': return left > right;
        case '>=': return left >= right;
        case '<': return left < right;
        case '<=': return left <= right;
        default: return false;
    }
}

// Helper function to evaluate JavaScript expressions
function evaluateExpression(expression) {
    try {
        // Create a safe evaluation context with workflow variables
        const evalContext = { ...workflowVariables };
        
        // Build the function
        const evalFn = new Function('vars', `
            with (vars) {
                return !!(${expression});
            }
        `);
        
        return evalFn(evalContext);
    } catch (error) {
        console.error('Expression evaluation error:', error);
        return false;
    }
}

async function executeLoopNode(config, node, prev_data = {}) {
    try {
        // Initialize loop tracking
        if (!window.activeLoops) {
            window.activeLoops = new Map();
        }
        if (!window.loopResults) {
            window.loopResults = new Map();
        }
        if (!window.completedLoops) {
            window.completedLoops = new Set();
        }
        
        // Determine the source for iteration
        let items = [];
        let sourceDescription = 'Unknown source';
        
        if (config.sourceType === 'auto') {
            // Auto-detect arrays in the previous data
            const detected = autoDetectArray(prev_data);
            if (detected.array) {
                items = detected.array;
                sourceDescription = `Auto-detected: ${detected.description}`;
            }
        } else if (config.sourceType === 'variable') {
            const varValue = workflowVariables[config.sourceVariable];
            if (Array.isArray(varValue)) {
                items = varValue;
                sourceDescription = `Variable: ${config.sourceVariable}`;
            } else if (varValue && typeof varValue === 'object') {
                items = Object.values(varValue);
                sourceDescription = `Variable (object values): ${config.sourceVariable}`;
            }
        } else if (config.sourceType === 'json') {
            try {
                items = JSON.parse(config.sourceJson);
                sourceDescription = 'JSON input';
            } catch (e) {
                if (isDebugMode) {
                    addDebugLogEntry(`Failed to parse JSON: ${e.message}`, 'error');
                }
            }
        }
        
        if (isDebugMode) {
            addDebugLogEntry(`Loop starting with ${items.length} items from ${sourceDescription}`, 'info');
            if (items.length > 0) {
                addDebugLogEntry(`Sample item: ${formatJsonOutput(items[0])}`, 'debug');
            }
        }
        
        // Find the loop body connection (should be 'pass' type)
        const connections = jsPlumbInstance.getConnections({ source: node.id });
        const loopBodyConnection = connections.find(conn => {
            const connType = conn.getData().type || 'pass';
            return connType === 'pass';
        });
        
        if (!loopBodyConnection || items.length === 0) {
            if (isDebugMode) {
                if (!loopBodyConnection) {
                    addDebugLogEntry('No loop body connection found. Connect nodes with pass connection.', 'warning');
                } else {
                    addDebugLogEntry('No items to iterate over.', 'info');
                }
            }
            
            // Mark loop as completed even if no iterations
            window.completedLoops.add(node.id);
            
            return {
                success: true,
                data: {
                    message: 'No items to process or no loop body connected',
                    _loopStats: {
                        totalItems: items.length,
                        processedItems: 0,
                        skippedItems: items.length,
                        source: sourceDescription
                    }
                }
            };
        }
        
        const maxIter = Math.min(items.length, config.maxIterations || 100);
        const results = [];
        
        // Store loop state
        window.activeLoops.set(node.id, {
            currentIndex: 0,
            totalItems: maxIter,
            items: items
        });
        
        // Visual indicator for loop node
        if (isDebugMode) {
            node.setAttribute('data-iteration', `0/${maxIter}`);
        }
        
        // Store original variable values
        const originalItemVar = workflowVariables[config.itemVariable];
        const originalIndexVar = workflowVariables[config.indexVariable];
        
        // Execute loop iterations
        for (let i = 0; i < maxIter; i++) {
            if (!isWorkflowRunning) {
                window.activeLoops.delete(node.id);
                throw new Error('Workflow execution stopped');
            }
            
            // Update loop state
            window.activeLoops.get(node.id).currentIndex = i;
            
            // Visual update for iteration counter
            if (isDebugMode) {
                node.setAttribute('data-iteration', `${i + 1}/${maxIter}`);
            }
            
            // Set loop variables
            workflowVariables[config.itemVariable] = items[i];
            workflowVariables[config.indexVariable] = i;
            workflowVariables['_loopStats'] = {
                currentIndex: i,
                totalItems: items.length,
                processedItems: i + 1,
                isLastItem: i === maxIter - 1
            };
            
            if (isDebugMode) {
                addDebugLogEntry(`═══════════════════════════════════════`, 'info');
                addDebugLogEntry(`Loop iteration ${i + 1}/${maxIter}`, 'info');
                addDebugLogEntry(`Current item (${config.itemVariable}): ${formatJsonOutput(items[i])}`, 'debug');
                updateVariablesTable();
            }
            
            // Execute the loop body
            const loopBodyNode = document.getElementById(loopBodyConnection.targetId);
            if (loopBodyNode) {
                // Execute the loop body branch until it hits an End Loop node
                const loopResult = await executeLoopBody(loopBodyNode, {
                    ...prev_data,
                    _loopItem: items[i],
                    _loopIndex: i,
                    _loopTotal: maxIter
                }, node.id);
                
                // Collect results based on output mode
                if (config.outputMode === 'array') {
                    results.push(loopResult);
                } else if (config.outputMode === 'last') {
                    results[0] = loopResult;
                } else if (config.outputMode === 'concat') {
                    // Concatenate string results
                    const str = typeof loopResult === 'string' ? loopResult : 
                               loopResult?.data ? JSON.stringify(loopResult.data) : 
                               JSON.stringify(loopResult);
                    results.push(str);
                } else if (config.outputMode === 'merge' && typeof loopResult === 'object') {
                    // Merge object results
                    Object.assign(results, loopResult);
                }
                
                if (isDebugMode) {
                    addDebugLogEntry(`Iteration ${i + 1} completed`, 'success');
                }
            }
        }
        
        // Restore original variable values
        if (originalItemVar !== undefined) {
            workflowVariables[config.itemVariable] = originalItemVar;
        } else {
            delete workflowVariables[config.itemVariable];
        }
        
        if (originalIndexVar !== undefined) {
            workflowVariables[config.indexVariable] = originalIndexVar;
        } else {
            delete workflowVariables[config.indexVariable];
        }
        
        // Clean up loop state
        window.activeLoops.delete(node.id);
        node.removeAttribute('data-iteration');
        
        // Prepare final output based on output mode
        let finalOutput;
        if (config.outputMode === 'concat') {
            finalOutput = results.join(config.concatSeparator || '');
        } else if (config.outputMode === 'merge') {
            finalOutput = results;
        } else {
            finalOutput = results;
        }
        
        // Store results for End Loop node
        window.loopResults.set(node.id, finalOutput);
        
        // IMPORTANT: Mark this loop as completed so we don't execute the body again
        window.completedLoops.add(node.id);
        
        if (isDebugMode) {
            addDebugLogEntry(`═══════════════════════════════════════`, 'info');
            addDebugLogEntry(`Loop completed: ${maxIter} iterations processed`, 'success');
            addDebugLogEntry(`Final output: ${formatJsonOutput(finalOutput)}`, 'debug');
            updateVariablesTable();
        }
        
        // Find and execute the End Loop node to continue the workflow
        const endLoopNode = findEndLoopNode(node.id);
        if (endLoopNode) {
            if (isDebugMode) {
                addDebugLogEntry(`Found End Loop node, continuing workflow from there`, 'info');
            }
            // Store a flag to indicate we should continue from End Loop
            node.setAttribute('data-continue-from-end-loop', endLoopNode.id);
        }
        
        return {
            success: true,
            data: finalOutput,
            skipLoopBody: true  // Flag to indicate loop body should not be executed again
        };
        
    } catch (error) {
        // Clean up on error
        if (window.activeLoops) {
            window.activeLoops.delete(node.id);
        }
        if (window.completedLoops) {
            window.completedLoops.delete(node.id);
        }
        node.removeAttribute('data-iteration');
        node.removeAttribute('data-continue-from-end-loop');
        
        if (isDebugMode) {
            addDebugLogEntry(`Loop error: ${error.message}`, 'error');
        }
        
        return {
            success: false,
            error: error.message,
            data: prev_data
        };
    }
}

// Helper function to find the End Loop node connected to a Loop node
function findEndLoopNode(loopNodeId) {
    // Use BFS to find the End Loop node in the loop body
    const visited = new Set();
    const queue = [];
    
    // Start with connections from the loop node
    const connections = jsPlumbInstance.getConnections({ source: loopNodeId });
    for (const conn of connections) {
        if (conn.getData().type === 'pass') {
            queue.push(conn.targetId);
        }
    }
    
    while (queue.length > 0) {
        const nodeId = queue.shift();
        if (visited.has(nodeId)) continue;
        visited.add(nodeId);
        
        const node = document.getElementById(nodeId);
        if (!node) continue;
        
        // Check if this is an End Loop node
        if (node.getAttribute('data-type') === 'End Loop') {
            return node;
        }
        
        // Add connected nodes to queue
        const nextConnections = jsPlumbInstance.getConnections({ source: nodeId });
        for (const conn of nextConnections) {
            if (!visited.has(conn.targetId)) {
                queue.push(conn.targetId);
            }
        }
    }
    
    return null;
}

// Additional CSS for active node highlighting (add to your CSS)
const loopDebugStyles = `
/* Active node highlighting in debug mode */


/* Loop iteration counter badge */
.workflow-node[data-type="Loop"][data-iteration]::after {
    content: attr(data-iteration);
    position: absolute;
    top: -15px;
    right: -15px;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    border-radius: 12px;
    padding: 4px 8px;
    font-size: 11px;
    font-weight: bold;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
    z-index: 1000;
}
`;

// Add styles to document if not already present
if (!document.getElementById('loop-debug-styles')) {
    const styleElement = document.createElement('style');
    styleElement.id = 'loop-debug-styles';
    styleElement.textContent = loopDebugStyles;
    document.head.appendChild(styleElement);
}

// Modified executeLoopBody function with proper debug logging
async function executeLoopBody(node, data, loopNodeId, visitedNodes = new Set()) {
    // Check if this is an End Loop node
    if (node.getAttribute('data-type') === 'End Loop') {
        if (isDebugMode) {
            addDebugLogEntry('Reached End Loop node, returning to loop', 'debug');
        }
        return data; // Don't execute End Loop during iterations
    }
    
    // Prevent infinite loops within the loop body
    if (visitedNodes.has(node.id)) {
        return data;
    }
    visitedNodes.add(node.id);
    
    const nodeId = node.id;
    const nodeName = node.querySelector('.node-content').textContent.trim();
    const nodeType = node.getAttribute('data-type');
    
    // Add debug logging for loop body execution
    if (isDebugMode) {
        // Mark node as currently executing
        node.classList.add('debug-current');
        addDebugLogEntry(`[Loop Body] Executing node: ${nodeName} (${nodeType})`, 'info');
        
        const config = nodeConfigs.get(nodeId) || {};
        if (Object.keys(config).length > 0) {
            addDebugLogEntry(`[Loop Body] Node configuration: ${formatJsonOutput(config)}`, 'debug');
        }
        
        if (Object.keys(data).length > 0) {
            addDebugLogEntry(`[Loop Body] Input data: ${formatJsonOutput(data)}`, 'debug');
        }
    }
    
    // Mark node as executing for visual feedback
    node.classList.add('executing');
    node.classList.remove('completed', 'error');
    
    try {
        // Execute the current node
        const result = await executeNodeAction(node, data);
        
        // Mark node as completed
        node.classList.remove('executing');
        node.classList.add('completed');
        
        // Debug logging for result
        if (isDebugMode) {
            node.classList.remove('debug-current');
            
            if (result.success) {
                addDebugLogEntry(`[Loop Body] Node ${nodeName} completed successfully`, 'success');
                if (result.data && Object.keys(result.data).length > 0) {
                    addDebugLogEntry(`[Loop Body] Output data: ${formatJsonOutput(result.data)}`, 'debug');
                }
            } else {
                addDebugLogEntry(`[Loop Body] Node ${nodeName} failed: ${result.error}`, 'error');
            }
            
            // Store node output for debug panel
            nodeOutputs[nodeId] = {
                name: nodeName,
                data: result.data || {},
                success: result.success,
                error: result.error
            };
            
            // Update execution path
            executionPath.push({
                nodeId: nodeId,
                nodeName: nodeName,
                success: result.success
            });
            
            // Update debug panel UI
            updateNodeOutputSelect();
            updateExecutionPath();
            updateVariablesTable();
        }
        
        // Find next nodes in the loop body
        const connections = jsPlumbInstance.getConnections({ source: node.id });
        
        for (const conn of connections) {
            const connType = conn.getData().type || 'pass';
            const shouldContinue = (connType === 'pass' && result.success) || 
                                  (connType === 'fail' && !result.success);
            
            if (shouldContinue) {
                const nextNode = document.getElementById(conn.targetId);
                
                if (nextNode) {
                    if (isDebugMode) {
                        addDebugLogEntry(`[Loop Body] Following ${connType} path to: ${nextNode.querySelector('.node-content').textContent.trim()}`, 'info');
                    }
                    
                    // Continue executing the loop body
                    const branchResult = await executeLoopBody(
                        nextNode, 
                        result.data || data, 
                        loopNodeId, 
                        visitedNodes
                    );
                    
                    return branchResult;
                }
            }
        }
        
        return result.data || data;
        
    } catch (error) {
        // Error handling
        node.classList.remove('executing');
        node.classList.add('error');
        
        if (isDebugMode) {
            node.classList.remove('debug-current');
            addDebugLogEntry(`[Loop Body] Error in node ${nodeName}: ${error.message}`, 'error');
            
            nodeOutputs[nodeId] = {
                name: nodeName,
                data: {},
                success: false,
                error: error.message
            };
            
            updateNodeOutputSelect();
            updateExecutionPath();
        }
        
        throw error;
    }
}

// Helper function: Auto-detect arrays in data
function autoDetectArray(data) {
    // Priority order for array detection
    
    // 1. Check if data itself is an array
    if (Array.isArray(data)) {
        return { array: data, description: 'Direct array input' };
    }
    
    // 2. Check for Folder Selector pattern
    if (data.allFiles && Array.isArray(data.allFiles)) {
        return { array: data.allFiles, description: 'Folder Selector: allFiles' };
    }
    
    // 3. Check for database query results
    if (data.results && Array.isArray(data.results)) {
        return { array: data.results, description: 'Database query results' };
    }
    
    // 4. Check for data.data (nested structure)
    if (data.data) {
        if (Array.isArray(data.data)) {
            return { array: data.data, description: 'data property' };
        }
        if (data.data.allFiles && Array.isArray(data.data.allFiles)) {
            return { array: data.data.allFiles, description: 'Nested Folder Selector: allFiles' };
        }
        if (data.data.results && Array.isArray(data.data.results)) {
            return { array: data.data.results, description: 'Nested results' };
        }
    }
    
    // 5. Check for common array property names
    const commonArrayProps = ['items', 'records', 'rows', 'documents', 'files', 'list', 'array', 'collection'];
    for (const prop of commonArrayProps) {
        if (data[prop] && Array.isArray(data[prop])) {
            return { array: data[prop], description: `Property: ${prop}` };
        }
    }
    
    // 6. Find first array property (deep search)
    const firstArray = findFirstArray(data, 3); // Max depth 3
    if (firstArray) {
        return firstArray;
    }
    
    // No array found
    return { array: [], description: 'No array found' };
}

// Helper function: Deep search for first array
function findFirstArray(obj, maxDepth, currentDepth = 0, path = '') {
    if (currentDepth >= maxDepth || !obj || typeof obj !== 'object') {
        return null;
    }
    
    for (const [key, value] of Object.entries(obj)) {
        const currentPath = path ? `${path}.${key}` : key;
        
        if (Array.isArray(value) && value.length > 0) {
            return { array: value, description: `Auto-found at: ${currentPath}` };
        }
        
        if (typeof value === 'object' && value !== null) {
            const found = findFirstArray(value, maxDepth, currentDepth + 1, currentPath);
            if (found) return found;
        }
    }
    
    return null;
}

// Helper function: Provide helpful suggestions
function getSuggestionForSource(data) {
    const suggestions = [];
    
    if (data.allFiles) {
        suggestions.push('Try setting Source Type to "Folder Files"');
    }
    
    if (data.selectedFile && !data.allFiles) {
        suggestions.push('Folder Selector returned a single file. Configure it to process all files');
    }
    
    const arrayPaths = [];
    findArrayPaths(data, arrayPaths);
    
    if (arrayPaths.length > 0) {
        suggestions.push(`Found arrays at: ${arrayPaths.join(', ')}. Use "Output Path" source type`);
    }
    
    if (suggestions.length === 0) {
        suggestions.push('Previous node did not return an array. Consider using Set Variable to create one');
    }
    
    return suggestions.join('. ');
}

// Helper function: Find all array paths in an object
function findArrayPaths(obj, paths = [], currentPath = '', maxDepth = 3, currentDepth = 0) {
    if (currentDepth >= maxDepth || !obj || typeof obj !== 'object') {
        return;
    }
    
    for (const [key, value] of Object.entries(obj)) {
        const path = currentPath ? `${currentPath}.${key}` : key;
        
        if (Array.isArray(value)) {
            paths.push(path);
        } else if (typeof value === 'object' && value !== null) {
            findArrayPaths(value, paths, path, maxDepth, currentDepth + 1);
        }
    }
}

// Helper function: Update UI based on source type selection
function updateLoopSourceFields(sourceType) {
    const sourceGroup = document.getElementById('loop-source-group');
    const splitGroup = document.getElementById('split-config-group');
    const defaultGroup = document.getElementById('default-value-group');
    
    // Show/hide fields based on source type
    if (sourceType === 'auto') {
        sourceGroup.style.display = 'none';
        splitGroup.style.display = 'none';
    } else if (sourceType === 'split') {
        sourceGroup.style.display = 'block';
        splitGroup.style.display = 'block';
    } else if (sourceType === 'folderFiles') {
        sourceGroup.style.display = 'none';
        splitGroup.style.display = 'none';
    } else {
        sourceGroup.style.display = 'block';
        splitGroup.style.display = 'none';
    }
    
    // Show default value field if needed
    const emptyBehavior = document.querySelector('select[name="emptyBehavior"]');
    if (emptyBehavior) {
        defaultGroup.style.display = emptyBehavior.value === 'default' ? 'block' : 'none';
    }
}

async function executeEndLoopNode(config, node, prev_data = {}) {
    try {
        // Find the associated loop
        let loopNodeId = config.loopNodeId;
        
        if (!loopNodeId) {
            // Auto-detect: find the nearest Loop node that could reach this End Loop
            const loopNodes = document.querySelectorAll('.workflow-node[data-type="Loop"]');
            
            // For now, use the most recent active loop
            if (window.activeLoops && window.activeLoops.size > 0) {
                // Get the last active loop
                const activeLoopIds = Array.from(window.activeLoops.keys());
                loopNodeId = activeLoopIds[activeLoopIds.length - 1];
            }
        }
        
        // Check if we're inside a loop iteration
        if (window.activeLoops && window.activeLoops.has(loopNodeId)) {
            // We're inside a loop - just return the data to continue
            if (isDebugMode) {
                const loopState = window.activeLoops.get(loopNodeId);
                addDebugLogEntry(
                    `End Loop reached for iteration ${loopState.currentIndex + 1}/${loopState.totalItems}`, 
                    'debug'
                );
            }
            
            return {
                success: true,
                data: prev_data
            };
        }
        
        // We're not in a loop - this means the loop has completed
        // Get the loop results
        let loopResults = prev_data;
        if (window.loopResults && window.loopResults.has(loopNodeId)) {
            loopResults = window.loopResults.get(loopNodeId);
            window.loopResults.delete(loopNodeId);
        }
        
        // Log completion message if configured
        if (config.completionMessage) {
            const message = replaceVariableReferences(config.completionMessage, workflowVariables);
            if (isDebugMode) {
                addDebugLogEntry(`Loop complete: ${message}`, 'success');
            }
        }
        
        // Continue with the accumulated loop results
        return {
            success: true,
            data: loopResults
        };
        
    } catch (error) {
        if (isDebugMode) {
            addDebugLogEntry(`End Loop error: ${error.message}`, 'error');
        }
        
        return {
            success: false,
            error: error.message,
            data: prev_data
        };
    }
}

// Make sure to also update stopWorkflow_Debug to handle the execution ID
function stopWorkflow_Debug() {
    isWorkflowRunning = false;
    
    // Clear the current execution ID
    currentExecutionId = null;
    
    // Reset UI
    document.getElementById('runWorkflowBtn').style.display = 'inline-block';
    document.getElementById('stopWorkflowBtn').style.display = 'none';
    document.getElementById('workflowStatus').style.display = 'none';

    const runBtn = document.getElementById('runWorkflowBtn');
    const stopBtn = document.getElementById('stopWorkflowBtn');
    
    stopBtn.classList.remove('active');
    runBtn.classList.add('active');
    
    // Reset all nodes' visual states
    document.querySelectorAll('.workflow-node').forEach(node => {
        node.classList.remove('executing', 'completed', 'error');
    });
    
    // Clear monitoring flags
    window.debugApprovalLinkShown = false;
    window.lastLoggedServerStep = null;
}

// Make sure currentExecutionId is declared globally if it isn't already
if (typeof currentExecutionId === 'undefined') {
    window.currentExecutionId = null;
}















/*

function visualizeWorkflowExecution(executionData) {
    // Reset all nodes' visual states
    document.querySelectorAll('.workflow-node').forEach(node => {
        node.classList.remove('executing', 'completed', 'error', 'paused');
    });
    
    // Add debug logging for execution status if in debug mode
    if (isDebugMode && executionData) {
        // Only log status changes to avoid spam
        if (!window.lastLoggedStatus || window.lastLoggedStatus !== executionData.status) {
            addDebugLogEntry(`📊 Workflow Status: ${executionData.status}`, 'info');
            window.lastLoggedStatus = executionData.status;
        }
    }
    
    // Get all step executions
    fetch(`/api/workflow/executions/${executionData.execution_id}/steps`)
        .then(response => response.json())
        .then(stepsData => {
            if (stepsData.status !== 'success' || !stepsData.steps) {
                return;
            }
            
            // Track which steps we've already logged to avoid duplicates
            if (!window.loggedSteps) {
                window.loggedSteps = new Set();
            }
            
            // Update node visualization based on step status
            stepsData.steps.forEach(step => {
                const node = document.getElementById(step.node_id);
                if (!node) return;
                
                // Create a unique key for this step and its status
                const stepKey = `${step.step_execution_id}_${step.status}`;
                
                // Log step execution in debug mode (only if not already logged)
                if (isDebugMode && !window.loggedSteps.has(stepKey)) {
                    window.loggedSteps.add(stepKey);
                    
                    // Determine the appropriate log level based on status
                    let logLevel = 'info';
                    let statusEmoji = '▶️';
                    
                    switch(step.status.toLowerCase()) {
                        case 'running':
                            statusEmoji = '🔄';
                            logLevel = 'info';
                            break;
                        case 'completed':
                            statusEmoji = '✅';
                            logLevel = 'success';
                            break;
                        case 'approved':
                            statusEmoji = '✅';
                            logLevel = 'success';
                            break;
                        case 'failed':
                            statusEmoji = '❌';
                            logLevel = 'error';
                            break;
                        case 'rejected':
                            statusEmoji = '❌';
                            logLevel = 'error';
                            break;
                        case 'paused':
                            statusEmoji = '⏸️';
                            logLevel = 'warning';
                            break;
                        case 'pending':
                            statusEmoji = '⏳';
                            logLevel = 'debug';
                            break;
                        case 'skipped':
                            statusEmoji = '⏭️';
                            logLevel = 'debug';
                            break;
                    }
                    
                    // Log the step execution
                    addDebugLogEntry(
                        `${statusEmoji} Step: ${step.node_name} (${step.node_type}) - Status: ${step.status}`,
                        logLevel
                    );
                    
                    // If the step has output data, log it (for completed steps)
                    if (step.status.toLowerCase() === 'completed' && step.output_data) {
                        try {
                            const outputData = typeof step.output_data === 'string' 
                                ? JSON.parse(step.output_data) 
                                : step.output_data;
                            
                            if (outputData && Object.keys(outputData).length > 0) {
                                // Truncate large outputs for readability
                                const outputStr = JSON.stringify(outputData, null, 2);
                                if (outputStr.length > 500) {
                                    addDebugLogEntry(
                                        `   Output: ${outputStr.substring(0, 500)}... (truncated)`,
                                        'debug'
                                    );
                                } else {
                                    addDebugLogEntry(
                                        `   Output: ${outputStr}`,
                                        'debug'
                                    );
                                }
                            }
                        } catch (e) {
                            // Ignore JSON parse errors for output data
                        }
                    }
                    
                    // Log error messages for failed steps
                    if (step.status.toLowerCase() === 'failed' && step.error_message) {
                        addDebugLogEntry(
                            `   Error: ${step.error_message}`,
                            'error'
                        );
                    }
                    
                    // Log duration for completed steps
                    if (step.started_at && step.completed_at) {
                        const startDate = new Date(step.started_at);
                        const endDate = new Date(step.completed_at);
                        const durationMs = endDate - startDate;
                        const durationSec = (durationMs / 1000).toFixed(2);
                        addDebugLogEntry(
                            `   Duration: ${durationSec}s`,
                            'debug'
                        );
                    }
                }
                
                // Remove existing states
                node.classList.remove('executing', 'completed', 'error', 'paused');
                
                // Add appropriate class for visual feedback
                switch(step.status.toLowerCase()) {
                    case 'running':
                        node.classList.add('executing');
                        break;
                    case 'completed':
                    case 'approved':
                        node.classList.add('completed');
                        break;
                    case 'failed':
                    case 'rejected':
                        node.classList.add('error');
                        break;
                    case 'paused':
                        node.classList.add('paused');
                        break;
                }
            });
            
            // Log when workflow is waiting for approval
            if (isDebugMode && executionData.status === 'Paused') {
                const approvalStep = stepsData.steps.find(
                    s => s.status === 'Paused' && s.node_type === 'Human Approval'
                );
                if (approvalStep && !window.lastLoggedApproval) {
                    addDebugLogEntry(
                        `⏸️ Workflow paused - Waiting for Human Approval at: ${approvalStep.node_name}`,
                        'warning'
                    );
                    addDebugLogEntry(
                        `   <a href="/monitoring" target="_blank">Open Monitoring Dashboard to approve/reject</a>`,
                        'warning'
                    );
                    window.lastLoggedApproval = approvalStep.step_execution_id;
                }
            }
            
            // Log workflow completion
            if (isDebugMode && executionData.status === 'Completed' && !window.workflowCompletionLogged) {
                addDebugLogEntry('🎉 Workflow execution completed successfully!', 'success');
                window.workflowCompletionLogged = true;
                
                // Log summary
                const totalSteps = stepsData.steps.length;
                const completedSteps = stepsData.steps.filter(s => s.status === 'Completed').length;
                const failedSteps = stepsData.steps.filter(s => s.status === 'Failed').length;
                
                addDebugLogEntry(
                    `   Summary: ${completedSteps}/${totalSteps} steps completed, ${failedSteps} failed`,
                    'info'
                );
            }
            
            // Log workflow failure
            if (isDebugMode && executionData.status === 'Failed' && !window.workflowFailureLogged) {
                addDebugLogEntry('❌ Workflow execution failed!', 'error');
                window.workflowFailureLogged = true;
                
                // Find and log the failed step
                const failedStep = stepsData.steps.find(s => s.status === 'Failed');
                if (failedStep) {
                    addDebugLogEntry(
                        `   Failed at: ${failedStep.node_name} (${failedStep.node_type})`,
                        'error'
                    );
                }
            }
        })
        .catch(error => {
            console.error('Error fetching step executions:', error);
            if (isDebugMode) {
                addDebugLogEntry(`⚠️ Error fetching execution steps: ${error.message}`, 'error');
            }
        });
}

// Helper function to reset debug tracking when starting a new workflow
function resetDebugTracking() {
    window.loggedSteps = new Set();
    window.lastLoggedStatus = null;
    window.lastLoggedApproval = null;
    window.workflowCompletionLogged = false;
    window.workflowFailureLogged = false;
}

// Update startWorkflow to reset tracking
const originalStartWorkflow = window.startWorkflow || function() {};
window.startWorkflow = function() {
    // Reset debug tracking for new execution
    if (isDebugMode) {
        resetDebugTracking();
        addDebugLogEntry('🚀 Starting workflow execution...', 'info');
    }
    
    // Call original function
    return originalStartWorkflow.apply(this, arguments);
};

*/




// ============================================
// PART 1: Updated visualizeWorkflowExecution function with Variables Support
// ============================================

function visualizeWorkflowExecution(executionData) {
    // Reset all nodes' visual states
    // document.querySelectorAll('.workflow-node').forEach(node => {
    //     node.classList.remove('executing', 'completed', 'error', 'paused');
    // });
    
    // Add debug logging for execution status if in debug mode
    if (isDebugMode && executionData) {
        // Only log status changes to avoid spam
        if (!window.lastLoggedStatus || window.lastLoggedStatus !== executionData.status) {
            addDebugLogEntry(`📊 Workflow Status: ${executionData.status}`, 'info');
            window.lastLoggedStatus = executionData.status;
        }
    }
    
    // NEW: Fetch and update variables if in debug mode
    if (isDebugMode && executionData && executionData.execution_id) {
        updateWorkflowVariablesFromServer(executionData.execution_id);
    }
    
    // Get all step executions
    fetch(`/api/workflow/executions/${executionData.execution_id}/steps`)
        .then(response => response.json())
        .then(stepsData => {
            if (stepsData.status !== 'success' || !stepsData.steps) {
                return;
            }
            
            // Track which steps we've already logged to avoid duplicates
            if (!window.loggedSteps) {
                window.loggedSteps = new Set();
            }
            
            // Update node visualization based on step status
            stepsData.steps.forEach(step => {
                const node = document.getElementById(step.node_id);
                if (!node) return;

                // Safety check: count how many status classes this node has
                const statusClasses = ['executing', 'completed', 'error', 'paused'];
                const currentStatusClasses = statusClasses.filter(c => node.classList.contains(c));
                
                // If node has multiple status classes, clean it up
                if (currentStatusClasses.length > 1) {
                    console.warn(`Node ${step.node_id} has multiple status classes:`, currentStatusClasses);
                    node.classList.remove('executing', 'completed', 'error', 'paused');
                }
                
                // Create a unique key for this step and its status
                const stepKey = `${step.step_execution_id}_${step.status}`;
                
                // Log step execution in debug mode (only if not already logged)
                if (isDebugMode && !window.loggedSteps.has(stepKey)) {
                    window.loggedSteps.add(stepKey);
                    
                    // Determine the appropriate log level based on status
                    let logLevel = 'info';
                    let statusEmoji = '▶️';
                    
                    switch(step.status.toLowerCase()) {
                        case 'running':
                            statusEmoji = '🔄';
                            logLevel = 'info';
                            break;
                        case 'completed':
                            statusEmoji = '✅';
                            logLevel = 'success';
                            break;
                        case 'approved':
                            statusEmoji = '✅';
                            logLevel = 'success';
                            break;
                        case 'failed':
                            statusEmoji = '❌';
                            logLevel = 'error';
                            break;
                        case 'rejected':
                            statusEmoji = '❌';
                            logLevel = 'error';
                            break;
                        case 'paused':
                            statusEmoji = '⏸️';
                            logLevel = 'warning';
                            break;
                        case 'pending':
                            statusEmoji = '⏳';
                            logLevel = 'debug';
                            break;
                        case 'skipped':
                            statusEmoji = '⏭️';
                            logLevel = 'debug';
                            break;
                    }
                    
                    // Log the step execution
                    addDebugLogEntry(
                        `${statusEmoji} Step: ${step.node_name} (${step.node_type}) - Status: ${step.status}`,
                        logLevel
                    );
                    
                    // If the step has output data, log it (for completed steps)
                    if (step.status.toLowerCase() === 'completed' && step.output_data) {
                        try {
                            const outputData = typeof step.output_data === 'string' 
                                ? JSON.parse(step.output_data) 
                                : step.output_data;
                            
                            if (outputData && Object.keys(outputData).length > 0) {
                                // Truncate large outputs for readability
                                const outputStr = JSON.stringify(outputData, null, 2);
                                if (outputStr.length > 500) {
                                    addDebugLogEntry(
                                        `   Output: ${outputStr.substring(0, 500)}... (truncated)`,
                                        'debug'
                                    );
                                } else {
                                    addDebugLogEntry(
                                        `   Output: ${outputStr}`,
                                        'debug'
                                    );
                                }
                            }
                        } catch (e) {
                            // Ignore JSON parse errors for output data
                        }
                    }
                    
                    // Log error messages for failed steps
                    if (step.status.toLowerCase() === 'failed' && step.error_message) {
                        addDebugLogEntry(
                            `   Error: ${step.error_message}`,
                            'error'
                        );
                    }
                    
                    // Log duration for completed steps
                    if (step.started_at && step.completed_at) {
                        const startDate = new Date(step.started_at);
                        const endDate = new Date(step.completed_at);
                        const durationMs = endDate - startDate;
                        const durationSec = (durationMs / 1000).toFixed(2);
                        addDebugLogEntry(
                            `   Duration: ${durationSec}s`,
                            'debug'
                        );
                    }
                }

                // ============================================
                // Update classes if they changed
                // ============================================
                const currentClasses = node.classList;
                const desiredStatus = step.status.toLowerCase();
                
                // Check if node already has the correct status class
                const hasCorrectClass = (
                    (desiredStatus === 'running' && currentClasses.contains('executing')) ||
                    (desiredStatus === 'completed' && currentClasses.contains('completed')) ||
                    (desiredStatus === 'failed' && currentClasses.contains('error')) ||
                    (desiredStatus === 'paused' && currentClasses.contains('paused'))
                );
                
                if (!hasCorrectClass) {
                    // Remove existing states
                    node.classList.remove('executing', 'completed', 'error', 'paused');
                    
                    // Add appropriate class for visual feedback
                    switch(step.status.toLowerCase()) {
                        case 'running':
                            node.classList.add('executing');
                            break;
                        case 'completed':
                        case 'approved':
                            node.classList.add('completed');
                            break;
                        case 'failed':
                        case 'rejected':
                            node.classList.add('error');
                            break;
                        case 'paused':
                            node.classList.add('paused');
                            break;
                    }
                }
            });
            
            // Log when workflow is waiting for approval
            if (isDebugMode && executionData.status === 'Paused') {
                const approvalStep = stepsData.steps.find(
                    s => s.status === 'Paused' && s.node_type === 'Human Approval'
                );
                if (approvalStep && !window.lastLoggedApproval) {
                    addDebugLogEntry(
                        `⏸️ Workflow paused - Waiting for Human Approval at: ${approvalStep.node_name}`,
                        'warning'
                    );
                    addDebugLogEntry(
                        `   <a href="/monitoring" target="_blank">Open Monitoring Dashboard to approve/reject</a>`,
                        'warning'
                    );
                    window.lastLoggedApproval = approvalStep.step_execution_id;
                }
            }
            
            // Log workflow completion
            if (isDebugMode && executionData.status === 'Completed' && !window.workflowCompletionLogged) {
                addDebugLogEntry('🎉 Workflow execution completed successfully!', 'success');
                window.workflowCompletionLogged = true;
                
                // Log summary
                const totalSteps = stepsData.steps.length;
                const completedSteps = stepsData.steps.filter(s => s.status === 'Completed').length;
                const failedSteps = stepsData.steps.filter(s => s.status === 'Failed').length;
                
                addDebugLogEntry(
                    `   Summary: ${completedSteps}/${totalSteps} steps completed, ${failedSteps} failed`,
                    'info'
                );
            }
            
            // Log workflow failure
            if (isDebugMode && executionData.status === 'Failed' && !window.workflowFailureLogged) {
                addDebugLogEntry('❌ Workflow execution failed!', 'error');
                window.workflowFailureLogged = true;
                
                // Find and log the failed step
                const failedStep = stepsData.steps.find(s => s.status === 'Failed');
                if (failedStep) {
                    addDebugLogEntry(
                        `   Failed at: ${failedStep.node_name} (${failedStep.node_type})`,
                        'error'
                    );
                }
            }
        })
        .catch(error => {
            console.error('Error fetching step executions:', error);
            if (isDebugMode) {
                addDebugLogEntry(`⚠️ Error fetching execution steps: ${error.message}`, 'error');
            }
        });
}

// ============================================
// PART 2: New function to fetch and update variables from server
// ============================================

function updateWorkflowVariablesFromServer(executionId) {
    if (!executionId) return;
    
    // Fetch variables from the server
    fetch(`/api/workflow/executions/${executionId}/variables`)
        .then(response => {
            if (!response.ok) {
                throw new Error(`Failed to fetch variables: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (data.status === 'success' && data.variables) {
                // Track which variables have changed
                const changedVariables = [];
                
                // Update workflowVariables with server data
                Object.entries(data.variables).forEach(([name, varData]) => {
                    const oldValue = workflowVariables[name];
                    const newValue = varData.value;
                    
                    // Check if the value has changed
                    if (JSON.stringify(oldValue) !== JSON.stringify(newValue)) {
                        workflowVariables[name] = newValue;
                        changedVariables.push({
                            name: name,
                            oldValue: oldValue,
                            newValue: newValue,
                            type: varData.type
                        });
                    }
                });
                
                // Log variable changes in debug mode
                if (isDebugMode && changedVariables.length > 0) {
                    // Only log if we haven't logged these exact changes before
                    const changeKey = JSON.stringify(changedVariables);
                    if (!window.lastLoggedVariableChanges || window.lastLoggedVariableChanges !== changeKey) {
                        window.lastLoggedVariableChanges = changeKey;
                        
                        changedVariables.forEach(change => {
                            addDebugLogEntry(
                                `📝 Variable updated: "${change.name}" = ${formatVariableValueForLog(change.newValue)}`,
                                'info'
                            );
                        });
                    }
                }
                
                // Update the variables table in the debug panel
                updateVariablesTableFromServer(data.variables);
            }
        })
        .catch(error => {
            console.error('Error fetching workflow variables:', error);
            // Don't log this error in debug mode as it might be too noisy
        });
}

// ============================================
// PART 3: Updated variables table function that handles server data
// ============================================

function updateVariablesTableFromServer(serverVariables) {
    const tableBody = document.getElementById('variables-table-body');
    if (!tableBody) return;
    
    tableBody.innerHTML = '';
    
    // Combine server variables with local workflow variables
    const allVariables = { ...workflowVariables };
    
    // Override with server values if they exist
    if (serverVariables) {
        Object.entries(serverVariables).forEach(([name, varData]) => {
            allVariables[name] = varData.value;
        });
    }
    
    // Add row for each variable
    Object.entries(allVariables).forEach(([name, value]) => {
        const tr = document.createElement('tr');
        
        // Get type from server data or definition if available
        let type = 'unknown';
        if (serverVariables && serverVariables[name]) {
            type = serverVariables[name].type || typeof value;
        } else if (workflowVariableDefinitions && workflowVariableDefinitions[name]) {
            type = workflowVariableDefinitions[name].type || typeof value;
        } else {
            type = typeof value;
        }
        
        // Format the value for display
        let displayValue = formatVariableValue(value);
        
        // Add a visual indicator if this is a recently changed variable
        let nameDisplay = name;
        if (window.recentlyChangedVariables && window.recentlyChangedVariables.has(name)) {
            nameDisplay = `${name} <span class="badge bg-warning ms-1">updated</span>`;
        }
        
        tr.innerHTML = `
            <td>${nameDisplay}</td>
            <td><span class="badge bg-secondary">${type}</span></td>
            <td class="text-break">${displayValue}</td>
        `;
        
        tableBody.appendChild(tr);
    });
    
    // If empty, show message
    if (Object.keys(allVariables).length === 0) {
        const tr = document.createElement('tr');
        tr.innerHTML = '<td colspan="3" class="text-center text-muted">No variables defined yet</td>';
        tableBody.appendChild(tr);
    }
}

// ============================================
// PART 4: Helper functions for formatting
// ============================================

// Format variable value for logging
function formatVariableValueForLog(value) {
    if (value === null) return 'null';
    if (value === undefined) return 'undefined';
    
    if (typeof value === 'object') {
        const str = JSON.stringify(value);
        if (str.length > 100) {
            return str.substring(0, 100) + '...';
        }
        return str;
    }
    
    if (typeof value === 'string' && value.length > 100) {
        return `"${value.substring(0, 100)}..."`;
    }
    
    return JSON.stringify(value);
}

// Keep existing formatVariableValue function for display
function formatVariableValue(value) {
    if (value === null) return '<em class="text-muted">null</em>';
    if (value === undefined) return '<em class="text-muted">undefined</em>';
    
    if (typeof value === 'object') {
        try {
            const formatted = JSON.stringify(value, null, 2);
            if (formatted.length > 500) {
                return `<pre class="mb-0">${formatted.substring(0, 500)}...</pre>`;
            }
            return `<pre class="mb-0">${formatted}</pre>`;
        } catch (e) {
            return '<em class="text-muted">Error formatting object</em>';
        }
    }
    
    if (typeof value === 'string' && value.length > 200) {
        return `${value.substring(0, 200)}...`;
    }
    
    return String(value);
}

// ============================================
// PART 5: Track recently changed variables
// ============================================

// Initialize tracking for recently changed variables
window.recentlyChangedVariables = new Set();

// Function to mark a variable as recently changed (visual feedback)
function markVariableAsChanged(variableName) {
    window.recentlyChangedVariables.add(variableName);
    
    // Remove the indicator after 3 seconds
    setTimeout(() => {
        window.recentlyChangedVariables.delete(variableName);
        // Re-render the table to remove the badge
        if (window.lastServerVariables) {
            updateVariablesTableFromServer(window.lastServerVariables);
        }
    }, 3000);
}

// ============================================
// PART 6: Integration with existing functions
// ============================================

// Helper function to reset debug tracking when starting a new workflow
function resetDebugTracking() {
    window.loggedSteps = new Set();
    window.lastLoggedStatus = null;
    window.lastLoggedApproval = null;
    window.workflowCompletionLogged = false;
    window.workflowFailureLogged = false;
    window.lastLoggedVariableChanges = null;
    window.recentlyChangedVariables = new Set();
    window.lastServerVariables = null;
}

// Update startWorkflow to reset tracking
const originalStartWorkflow = window.startWorkflow || function() {};
window.startWorkflow = function() {
    // Reset debug tracking for new execution
    if (isDebugMode) {
        resetDebugTracking();
        addDebugLogEntry('🚀 Starting workflow execution...', 'info');
    }
    
    // Call original function
    return originalStartWorkflow.apply(this, arguments);
};

function showFeedback(message, type = 'success') {
    const alertClass = type === 'success' ? 'alert-success' : 'alert-danger';
    const icon = type === 'success' ? 'check-circle' : 'exclamation-circle';
    
    const feedbackHtml = `
        <div class="alert ${alertClass} alert-dismissible fade show" role="alert">
            <i class="fas fa-${icon} mr-2"></i>${message}
            <button type="button" class="close" aria-label="Close" style="
                background: transparent;
                border: none;
                font-size: 1.5rem;
                line-height: 1;
                color: inherit;
                opacity: 0.5;
                padding: 0;
                margin-left: auto;
                cursor: pointer;
                transition: opacity 0.2s;
            " onmouseover="this.style.opacity='0.8'" onmouseout="this.style.opacity='0.5'">
                <span aria-hidden="true">&times;</span>
            </button>
        </div>
    `;
    
    const feedbackElement = document.getElementById('feedback-message');
    if (feedbackElement) {
        feedbackElement.innerHTML = feedbackHtml;
        
        const alertElement = feedbackElement.querySelector('.alert');
        
        // Handle close button click
        const closeButton = feedbackElement.querySelector('.close');
        if (closeButton) {
            closeButton.addEventListener('click', function() {
                if (alertElement) {
                    alertElement.classList.remove('show');
                    // Wait for CSS transition to complete before removing
                    setTimeout(() => alertElement.remove(), 150);
                }
            });
        }
        
        // Auto-dismiss after 5 seconds with fade effect
        setTimeout(() => {
            if (alertElement && alertElement.parentNode) {
                // Remove 'show' class to trigger fade out
                alertElement.classList.remove('show');
                
                // Remove the element after fade transition completes
                alertElement.addEventListener('transitionend', () => {
                    alertElement.remove();
                }, { once: true });
                
                // Fallback in case transitionend doesn't fire
                setTimeout(() => {
                    if (alertElement.parentNode) {
                        alertElement.remove();
                    }
                }, 200);
            }
        }, 5000);
    }
}

// Function to delete the currently loaded workflow
async function deleteCurrentWorkflow() {
    // Check if a workflow is currently loaded
    if (!currentWorkflowName) {
        showToast('No workflow is currently loaded', 'warning');
        return;
    }
    
    // Find the workflow ID from the dropdown or from saved state
    const workflowSelect = document.getElementById('workflowSelect');
    let workflowId = workflowSelect.value;
    
    // If no ID in dropdown, try to find it from the workflow list
    if (!workflowId) {
        try {
            const response = await fetch('/get/workflows');
            const data = await response.json();
            const workflows = typeof data === 'string' ? JSON.parse(data) : data;
            
            // Find workflow by name
            const currentWorkflow = workflows.find(w => 
                (w.workflow_name === currentWorkflowName) || 
                (w.name === currentWorkflowName)
            );
            
            if (currentWorkflow) {
                workflowId = currentWorkflow.id || currentWorkflow.ID;
            }
        } catch (error) {
            console.error('Error finding workflow ID:', error);
        }
    }
    
    if (!workflowId) {
        showToast('Cannot find workflow ID for deletion', 'error');
        return;
    }
    
    // Confirm deletion with the user
    const confirmed = await showConfirmDialog(
        'Delete Current Workflow',
        `Are you sure you want to delete "${currentWorkflowName}"? This action cannot be undone.`
    );
    
    if (!confirmed) {
        return;
    }
    
    try {
        // Stop workflow if it's running
        if (isWorkflowRunning) {
            stopWorkflow();
        }
        
        // Call the delete API
        const response = await fetch(`/delete/workflow/${workflowId}`, {
            method: 'DELETE'
        });
        
        if (!response.ok) {
            throw new Error(`Failed to delete workflow: ${response.statusText}`);
        }
        
        const result = await response.json();
        
        if (result.status === 'success') {
            showFeedback(`Workflow "${currentWorkflowName}" deleted successfully`, 'success');
            
            // Clear the current workflow
            createNewWorkflow(showConfirm=false);
            
            // Refresh the workflow dropdown
            await populateWorkflowsDropdown();
            
            // Hide the delete button
            document.getElementById('deleteCurrentWorkflowBtn').style.display = 'none';
        } else {
            throw new Error(result.message || 'Unknown error');
        }
    } catch (error) {
        console.error('Error deleting workflow:', error);
        showFeedback(`Error deleting workflow: ${error.message}`, 'error');
    }
}

// Update the updateCurrentWorkflowDisplay function to show/hide delete button
const originalUpdateCurrentWorkflowDisplay = updateCurrentWorkflowDisplay;
updateCurrentWorkflowDisplay = function() {
    originalUpdateCurrentWorkflowDisplay.apply(this, arguments);
    
    // Show or hide the delete button based on whether a workflow is loaded
    const deleteBtn = document.getElementById('deleteCurrentWorkflowBtn');
    if (deleteBtn) {
        if (currentWorkflowName) {
            deleteBtn.style.display = 'inline-block';
        } else {
            deleteBtn.style.display = 'none';
        }
    }
};

// Also update the loadSelectedWorkflow to show delete button
const originalLoadSelectedWorkflow2 = loadSelectedWorkflow;
loadSelectedWorkflow = async function() {
    try {
        await originalLoadSelectedWorkflow2.apply(this, arguments);
        // Show delete button after loading
        const deleteBtn = document.getElementById('deleteCurrentWorkflowBtn');
        if (deleteBtn && currentWorkflowName) {
            deleteBtn.style.display = 'inline-block';
        }
    } catch (error) {
        console.error('Error in loadSelectedWorkflow:', error);
    }
};

// Function to load assignees (users and groups)
async function loadAssignees() {
    try {
        const response = await fetch('/api/workflow/assignees');
        if (response.ok) {
            return await response.json();
        }
    } catch (error) {
        console.error('Failed to load assignees:', error);
    }
    return { users: [], groups: [] };
}

// Helper function to handle assignee type changes
function handleAssigneeTypeChange() {
    const typeSelect = document.getElementById('assigneeType');
    const assigneeGroup = document.getElementById('assigneeSelectGroup');
    const assigneeSelect = document.getElementById('assigneeId');
    const assigneeLabel = document.getElementById('assigneeLabel');
    const assigneeHelp = document.getElementById('assigneeHelp');
    
    if (!typeSelect || !assigneeGroup) return;
    
    const selectedType = typeSelect.value;
    
    // Reset and hide if no type selected
    if (!selectedType) {
        assigneeGroup.style.display = 'none';
        if (assigneeSelect) {
            assigneeSelect.innerHTML = '<option value="">-- Select --</option>';
            assigneeSelect.required = false;
        }
        return;
    }
    
    // Handle "unassigned" option
    if (selectedType === 'unassigned') {
        assigneeGroup.style.display = 'none';
        if (assigneeSelect) {
            assigneeSelect.required = false;
            assigneeSelect.value = '';
        }
        return;
    }
    
    // Show the selection dropdown for user/group
    assigneeGroup.style.display = 'block';
    
    // Update label based on type
    if (assigneeLabel) {
        assigneeLabel.textContent = selectedType === 'user' ? 'Select User' : 'Select Group';
    }
    
    // Make selection required
    if (assigneeSelect) {
        assigneeSelect.required = true;
        assigneeSelect.innerHTML = '<option value="">Loading...</option>';
    }
    
    // Load options
    loadAssigneeOptions(selectedType);
}

// Helper function to show notifications (if not already defined)
function showNotification(message, type = 'info') {
    const alertClass = {
        'success': 'alert-success',
        'error': 'alert-danger',
        'info': 'alert-info',
        'warning': 'alert-warning'
    }[type] || 'alert-info';
    
    const notification = document.createElement('div');
    notification.className = `alert ${alertClass} alert-dismissible fade show`;
    notification.style.cssText = 'position: fixed; top: 20px; right: 20px; z-index: 9999; min-width: 300px;';
    notification.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    
    document.body.appendChild(notification);
    
    setTimeout(() => {
        notification.remove();
    }, 5000);
}



// Initialize Human Approval configuration
function initializeHumanApprovalConfig(config) {
    // Load saved configuration if it exists
    if (config.assigneeType) {
        const typeSelect = document.getElementById('assigneeType');
        if (typeSelect) {
            typeSelect.value = config.assigneeType;
            handleAssigneeTypeChange();
        }
    }
    
    // Set up dueHours/timeoutMinutes sync
    const dueHoursInput = document.getElementById('dueHours');
    const timeoutMinutesInput = document.getElementById('timeoutMinutes');
    
    if (dueHoursInput && timeoutMinutesInput) {
        // If we have old timeoutMinutes but no dueHours, convert
        if (!config.dueHours && config.timeoutMinutes) {
            dueHoursInput.value = config.timeoutMinutes / 60;
        }
        
        // Sync the fields
        dueHoursInput.addEventListener('input', function() {
            const hours = parseFloat(this.value) || 0;
            timeoutMinutesInput.value = hours * 60;
        });
    }
    
    // Validate JSON in approval data field
    const approvalDataField = document.querySelector('textarea[name="approvalData"]');
    if (approvalDataField) {
        approvalDataField.addEventListener('blur', function() {
            try {
                // Try to parse JSON (ignoring variable references for validation)
                const testJson = this.value.replace(/\{\{[^}]+\}\}/g, '"test"');
                if (this.value.trim()) {
                    JSON.parse(testJson);
                }
                this.classList.remove('is-invalid');
            } catch (e) {
                this.classList.add('is-invalid');
            }
        });
    }
}

// Validate Human Approval configuration before saving
function validateHumanApprovalConfig() {
    const modalBody = document.getElementById('nodeConfigModalBody');
    
    // Check required title
    const titleInput = modalBody.querySelector('input[name="approvalTitle"]');
    if (!titleInput || !titleInput.value.trim()) {
        alert('Approval Title is required');
        if (titleInput) titleInput.focus();
        return false;
    }
    
    // Check assignee selection
    const typeSelect = modalBody.querySelector('select[name="assigneeType"]');
    if (typeSelect && (typeSelect.value === 'user' || typeSelect.value === 'group')) {
        const assigneeSelect = modalBody.querySelector('select[name="assigneeId"]');
        if (!assigneeSelect || !assigneeSelect.value) {
            alert('Please select an assignee');
            if (assigneeSelect) assigneeSelect.focus();
            return false;
        }
    }
    
    return true;
}

// Process Human Approval configuration for saving
function processHumanApprovalConfig(config) {
    // Convert string numbers to actual numbers
    if (config.priority !== undefined) {
        config.priority = parseInt(config.priority) || 0;
    }
    
    if (config.dueHours !== undefined && config.dueHours !== '') {
        config.dueHours = parseFloat(config.dueHours);
        // Sync with timeoutMinutes for backward compatibility
        config.timeoutMinutes = config.dueHours * 60;
    } else {
        config.dueHours = '';
    }
    
    // Ensure assignee fields are consistent
    if (!config.assigneeType) {
        config.assigneeId = '';
    }
    
    return config;
}


// Extend configureNode for Human Approval
(function() {
    const originalConfigureNode = window.configureNode;
    
    window.configureNode = function() {
        // Call original function
        originalConfigureNode.apply(this, arguments);
        
        // Only add our logic for Human Approval nodes
        if (window.configuredNode && window.configuredNode.getAttribute('data-type') === 'Human Approval') {
            // Wait for modal to be shown
            document.getElementById('nodeConfigModal').addEventListener('shown.bs.modal', function() {
                const config = nodeConfigs.get(window.configuredNode.id) || {};
                initializeHumanApprovalConfig(config);
            }, { once: true });
        }
    };
})();

// Extend saveNodeConfig for Human Approval
(function() {
    const originalSaveNodeConfig = window.saveNodeConfig;
    
    window.saveNodeConfig = function() {
        if (window.configuredNode && window.configuredNode.getAttribute('data-type') === 'Human Approval') {
            // Validate first
            if (!validateHumanApprovalConfig()) {
                return; // Don't save if validation fails
            }
            
            // Collect configuration
            const modalBody = document.getElementById('nodeConfigModalBody');
            const config = {};
            const inputs = modalBody.querySelectorAll('input, select, textarea');
            
            inputs.forEach(input => {
                if (input.type === 'checkbox') {
                    config[input.name] = input.checked;
                } else if (input.type === 'number') {
                    const value = input.value.trim();
                    config[input.name] = value === '' ? '' : Number(value);
                } else {
                    config[input.name] = input.value;
                }
            });
            
            // Process the config
            const processedConfig = processHumanApprovalConfig(config);
            
            // Save it
            nodeConfigs.set(window.configuredNode.id, processedConfig);
            
            // Mark as configured
            window.configuredNode.classList.add('configured');
            
            // Close modal
            const configModal = bootstrap.Modal.getInstance(document.getElementById('nodeConfigModal'));
            if (configModal) {
                configModal.hide();
            }
            
            return; // Don't call original for Human Approval
        }
        
        // Call original for other node types
        return originalSaveNodeConfig.apply(this, arguments);
    };
})();






// ============================================
// PART 3: CONFIGURATION HELPERS
// ============================================

function setupHumanApprovalConfig() {
    const assigneeType = document.getElementById('assigneeType');
    const assigneeGroup = document.getElementById('assigneeSelectGroup');
    
    if (assigneeType) {
        assigneeType.addEventListener('change', function() {
            if (this.value === 'user' || this.value === 'group') {
                assigneeGroup.style.display = 'block';
                // Optionally load users/groups via API
                loadAssigneeOptions(this.value);
            } else {
                assigneeGroup.style.display = 'none';
            }
        });
    }
    
    // Sync timeout fields
    const dueHours = document.querySelector('input[name="dueHours"]');
    const timeoutMinutes = document.querySelector('input[name="timeoutMinutes"]');
    
    if (dueHours && timeoutMinutes) {
        dueHours.addEventListener('input', function() {
            if (this.value) {
                timeoutMinutes.value = parseFloat(this.value) * 60;
            }
        });
        
        timeoutMinutes.addEventListener('input', function() {
            if (this.value) {
                dueHours.value = parseFloat(this.value) / 60;
            }
        });
    }
}

// Load users or groups from API
async function loadAssigneeOptions(type) {
    const assigneeSelect = document.getElementById('assigneeId');
    const assigneeHelp = document.getElementById('assigneeHelp');
    
    if (!assigneeSelect) return;
    
    try {
        const response = await fetch('/api/workflow/assignees');
        if (!response.ok) {
            throw new Error('Failed to load assignees');
        }
        
        const data = await response.json();
        
        // Clear and populate dropdown
        assigneeSelect.innerHTML = '<option value="">-- Select --</option>';
        
        if (type === 'user') {
            if (assigneeHelp) {
                assigneeHelp.textContent = 'Only users with End User role or higher can approve';
            }
            
            if (data.users && data.users.length > 0) {
                data.users.forEach(user => {
                    const option = document.createElement('option');
                    option.value = user.id;
                    option.textContent = user.label || `${user.name} (${user.username || user.user_name}) - ${user.role}`;
                    assigneeSelect.appendChild(option);
                });
            } else {
                assigneeSelect.innerHTML = '<option value="">No users available</option>';
            }
            
        } else if (type === 'group') {
            if (assigneeHelp) {
                assigneeHelp.textContent = 'All group members will be notified';
            }
            
            if (data.groups && data.groups.length > 0) {
                data.groups.forEach(group => {
                    const option = document.createElement('option');
                    option.value = group.id;
                    option.textContent = group.label || `${group.name} (${group.memberCount || group.member_count || 0} members)`;
                    assigneeSelect.appendChild(option);
                });
            } else {
                assigneeSelect.innerHTML = '<option value="">No groups available</option>';
            }
        }
        
        // Restore saved value if editing
        if (window.configuredNode) {
            const config = nodeConfigs.get(window.configuredNode.id) || {};
            if (config.assigneeId) {
                assigneeSelect.value = config.assigneeId;
            }
        }
        
    } catch (error) {
        console.error('Failed to load assignees:', error);
        assigneeSelect.innerHTML = '<option value="">Error loading options</option>';
        
        // If API fails, provide manual entry option
        if (assigneeHelp) {
            assigneeHelp.textContent = 'API unavailable - enter ID manually in approval data';
        }
    }
}

// Setup function for when modal opens
function setupHumanApprovalModal() {
    const assigneeType = document.getElementById('assigneeType');
    const dueHours = document.getElementById('dueHours');
    const timeoutMinutes = document.getElementById('timeoutMinutes');
    
    // Set up assignee type change handler
    if (assigneeType) {
        assigneeType.removeEventListener('change', handleAssigneeTypeChange); // Remove if exists
        assigneeType.addEventListener('change', handleAssigneeTypeChange);
        
        // Trigger initial setup if value exists
        if (assigneeType.value) {
            handleAssigneeTypeChange();
        }
    }
    
    // Sync timeout fields
    if (dueHours && timeoutMinutes) {
        dueHours.addEventListener('input', function() {
            const hours = parseFloat(this.value) || 0;
            timeoutMinutes.value = hours * 60;
        });
    }
    
    // Load saved configuration
    if (window.configuredNode) {
        const config = nodeConfigs.get(window.configuredNode.id) || {};
        
        // Restore assignee type and trigger loading
        if (config.assigneeType) {
            const typeSelect = document.getElementById('assigneeType');
            if (typeSelect) {
                typeSelect.value = config.assigneeType;
                handleAssigneeTypeChange();
            }
        }
    }
}


// ============================================
// PART 4: EXTEND configureNode (OPTIONAL)
// ============================================
(function() {
    const originalConfigureNode = window.configureNode;
    
    window.configureNode = function() {
        originalConfigureNode.apply(this, arguments);
        
        if (window.configuredNode && 
            window.configuredNode.getAttribute('data-type') === 'Human Approval') {
            
            // Setup when modal is shown
            const modal = document.getElementById('nodeConfigModal');
            if (modal) {
                modal.addEventListener('shown.bs.modal', setupHumanApprovalModal, { once: true });
            }
        }
    };
})();

// ============================================
// PART 5: VALIDATION IN saveNodeConfig
// ============================================
(function() {
    const originalSaveNodeConfig = window.saveNodeConfig;
    
    window.saveNodeConfig = function() {
        if (window.configuredNode && 
            window.configuredNode.getAttribute('data-type') === 'Human Approval') {
            
            const modalBody = document.getElementById('nodeConfigModalBody');
            
            // Validate required fields
            const titleInput = modalBody.querySelector('input[name="approvalTitle"]');
            if (!titleInput || !titleInput.value.trim()) {
                alert('Approval Title is required');
                if (titleInput) titleInput.focus();
                return;
            }
            
            // Validate assignment
            const typeSelect = modalBody.querySelector('select[name="assigneeType"]');
            if (!typeSelect || !typeSelect.value) {
                alert('Please select an assignment type (User, Group, or Available to All)');
                if (typeSelect) typeSelect.focus();
                return;
            }
            
            // If user or group selected, validate the selection
            if (typeSelect.value === 'user' || typeSelect.value === 'group') {
                const assigneeSelect = modalBody.querySelector('select[name="assigneeId"]');
                if (!assigneeSelect || !assigneeSelect.value) {
                    alert(`Please select a ${typeSelect.value}`);
                    if (assigneeSelect) assigneeSelect.focus();
                    return;
                }
            }
            
            // Collect configuration with proper type conversion
            const config = {};
            const inputs = modalBody.querySelectorAll('input, select, textarea');
            
            inputs.forEach(input => {
                if (input.type === 'checkbox') {
                    config[input.name] = input.checked;
                } else if (input.name === 'priority' || input.name === 'assigneeId') {
                    // Convert to number
                    const val = input.value.trim();
                    config[input.name] = val === '' ? '' : Number(val);
                } else if (input.name === 'dueHours' || input.name === 'timeoutMinutes') {
                    // Convert to number or keep empty
                    const val = input.value.trim();
                    config[input.name] = val === '' ? '' : Number(val);
                } else {
                    config[input.name] = input.value;
                }
            });
            
            // Validate approval data JSON if provided
            if (config.approvalData && config.approvalData.trim()) {
                try {
                    const testJson = config.approvalData.replace(/\{\{[^}]+\}\}/g, '"test"');
                    JSON.parse(testJson);
                } catch (e) {
                    alert('Invalid JSON in Approval Data field');
                    const jsonField = modalBody.querySelector('textarea[name="approvalData"]');
                    if (jsonField) jsonField.focus();
                    return;
                }
            }
            
            // Save configuration
            nodeConfigs.set(window.configuredNode.id, config);
            
            // Mark node as configured
            window.configuredNode.classList.add('configured');
            
            // Close modal
            const modal = bootstrap.Modal.getInstance(document.getElementById('nodeConfigModal'));
            if (modal) modal.hide();
            
            return; // Don't call original
        }
        
        // Call original for other node types
        return originalSaveNodeConfig.apply(this, arguments);
    };
})();


function setupHumanApprovalHandlers() {
    const assigneeType = document.getElementById('assigneeType');
    const assigneeGroup = document.getElementById('assigneeSelectGroup');
    const assigneeSelect = document.getElementById('assigneeId');
    
    if (!assigneeType) return;
    
    // Handle assignee type changes
    assigneeType.addEventListener('change', function() {
        const selectedType = this.value;
        
        if (selectedType === 'user' || selectedType === 'group') {
            // Show the dropdown
            assigneeGroup.style.display = 'block';
            assigneeSelect.required = true;
            
            // Load the users or groups
            loadAssigneeOptions(selectedType);
        } else {
            // Hide the dropdown
            assigneeGroup.style.display = 'none';
            assigneeSelect.required = false;
            assigneeSelect.value = '';
        }
    });
}

// Helper functions for the Execute Application node
function toggleExecuteAppFields() {
    const commandType = document.getElementById('command-type-select')?.value;
    // Additional UI logic if needed based on command type
}

function toggleOutputRegexField() {
    const outputParsing = document.querySelector('select[name="outputParsing"]')?.value;
    const regexGroup = document.getElementById('output-regex-group');
    if (regexGroup) {
        regexGroup.style.display = outputParsing === 'regex' ? 'block' : 'none';
    }
}


// Extend configureNode to populate Loop nodes for End Loop configuration
(function() {
    const originalConfigureNodeEndLoop = window.configureNode || (() => {});
    
    window.configureNode = function() {
        // Call the original/previous function
        originalConfigureNodeEndLoop.apply(this, arguments);
        
        // Check if this is an End Loop node
        if (configuredNode && configuredNode.getAttribute('data-type') === 'End Loop') {
            // Add event listener for when the modal is shown
            document.getElementById('nodeConfigModal').addEventListener('shown.bs.modal', function() {
                // Populate the Loop nodes dropdown
                populateLoopNodesDropdown();
                
                // Restore the saved selection if editing an existing End Loop node
                const config = nodeConfigs.get(configuredNode.id) || {};
                if (config.loopNodeId) {
                    const loopSelect = document.querySelector('select[name="loopNodeId"]');
                    if (loopSelect) {
                        // Set the value after a small delay to ensure options are loaded
                        setTimeout(() => {
                            loopSelect.value = config.loopNodeId;
                        }, 100);
                    }
                }
            }, { once: true });
        }
    };
})();

// Function to populate the Loop nodes dropdown
function populateLoopNodesDropdown() {
    const loopSelect = document.querySelector('select[name="loopNodeId"]');
    if (!loopSelect) return;
    
    // Save the currently selected value
    const selectedValue = loopSelect.value;
    
    // Clear existing options except the first (auto-detect) option
    while (loopSelect.options.length > 1) {
        loopSelect.remove(1);
    }
    
    // Find all Loop nodes in the workflow
    const loopNodes = document.querySelectorAll('.workflow-node[data-type="Loop"]');
    
    // Add each Loop node as an option
    loopNodes.forEach(loopNode => {
        const option = document.createElement('option');
        option.value = loopNode.id;
        
        // Get the node name from the content
        const nodeName = loopNode.querySelector('.node-content').textContent.trim();
        option.textContent = `${nodeName} (${loopNode.id})`;
        
        loopSelect.appendChild(option);
    });
    
    // Log for debugging
    console.log(`Populated End Loop dropdown with ${loopNodes.length} Loop nodes`);
    
    // Restore the selected value if it still exists
    if (selectedValue && Array.from(loopSelect.options).some(opt => opt.value === selectedValue)) {
        loopSelect.value = selectedValue;
    }
}


/**
 * Finalize training data capture when workflow is saved.
 * Call this AFTER successfully saving a workflow.
 * 
 * @param {Object} workflowState - Final workflow state (optional but recommended)
 * @param {string} workflowType - Classification like "document_processing" (optional)
 * @returns {Promise<boolean>} True if capture was finalized
 */
async function finalizeTrainingCapture(workflowState = null, workflowType = null) {
    try {
        console.log('Finalizing training data capture...');
        const response = await fetch('/api/workflow/builder/finalize-capture', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                workflow_state: workflowState,
                workflow_type: workflowType,
                success: true
            })
        });
        
        const data = await response.json();
        
        if (data.status === 'success' && data.captured) {
            console.log('Training data captured successfully');
        }
        console.log('Response received:');
        console.log(data);
        return data.captured || false;
        
    } catch (error) {
        // Non-critical - don't interrupt user workflow
        console.debug('Training capture finalization failed (non-critical):', error);
        return false;
    }
}

/**
 * Get training capture statistics.
 * Useful for monitoring data collection progress.
 */
async function getTrainingStats() {
    try {
        const response = await fetch('/api/workflow/builder/training-stats');
        const data = await response.json();
        
        if (data.status === 'success') {
            console.log('Training Capture Statistics:', data.statistics);
            return data.statistics;
        }
        
        return null;
    } catch (error) {
        console.debug('Could not fetch training stats:', error);
        return null;
    }
}


// Excel Export Node Template
nodeConfigTemplates['Excel Export'] = {
    template: `
        <div class="excel-export-config">
            <!-- Data Source -->
            <div class="mb-3">
                <label class="form-label fw-bold">
                    <i class="bi bi-box-arrow-right me-1"></i>Data Source
                </label>
                <div class="input-group">
                    <input type="text" class="form-control" name="inputVariable" id="excel-export-input-variable" 
                           placeholder="\${extractedData}" list="excel-export-variables-list">
                    <datalist id="excel-export-variables-list"></datalist>
                </div>
                <small class="form-text text-muted">Variable containing data to export (object or array)</small>
            </div>
            
            <!-- Flatten Array Option -->
            <div class="mb-3">
                <div class="form-check">
                    <input type="checkbox" class="form-check-input" name="flattenArray" id="excel-export-flatten">
                    <label class="form-check-label" for="excel-export-flatten">
                        <strong>Flatten array to multiple rows</strong>
                    </label>
                </div>
                <small class="form-text text-muted">If input is an array, write each item as a separate row</small>
            </div>
            
            <!-- Carry Forward Fields -->
            <div class="mb-3" id="excel-export-carry-forward-section">
                <label class="form-label fw-bold">Carry-Forward Fields</label>
                <div class="input-group">
                    <input type="text" class="form-control" name="carryForwardFields" id="excel-export-carry-forward" 
                           placeholder="record_id, customer, program_type">
                </div>
                <small class="form-text text-muted">
                    Comma-separated list of parent fields to include in each row
                </small>
            </div>
            
            <hr class="my-3">
            
            <!-- Excel Output Configuration -->
            <h6 class="fw-bold mb-3"><i class="bi bi-file-earmark-excel me-1 text-success"></i>Excel Output</h6>
            
            <!-- Operation Mode - NOW INCLUDES UPDATE -->
            <div class="mb-3">
                <label class="form-label fw-bold">Operation</label>
                <select class="form-select" name="excelOperation" id="excel-export-operation" 
                        onchange="ExcelExportNode.onOperationChange(this.value)">
                    <option value="new">Create New File</option>
                    <option value="template">New From Template</option>
                    <option value="append" selected>Append to Existing</option>
                    <option value="update">Update Existing (with Change Tracking)</option>
                </select>
                <small class="form-text text-muted" id="excel-export-operation-help" style="display:none;"></small>
            </div>
            
            <!-- Output Path -->
            <div class="mb-3">
                <label class="form-label fw-bold">Output File Path</label>
                <div class="input-group">
                    <input type="text" class="form-control" name="excelOutputPath" id="excel-export-output-path" 
                           placeholder="/output/results.xlsx">
                </div>
            </div>
            
            <!-- Template Path -->
            <div class="mb-3" id="excel-export-template-section">
                <label class="form-label fw-bold">Template/Source File Path</label>
                <div class="input-group">
                    <input type="text" class="form-control" name="excelTemplatePath" id="excel-export-template-path" 
                           placeholder="/templates/template.xlsx">
                </div>
            </div>
            
            <!-- Sheet Name -->
            <div class="mb-3">
                <label class="form-label fw-bold">Sheet Name</label>
                <input type="text" class="form-control" name="excelSheetName" id="excel-export-sheet-name" 
                       placeholder="Sheet1 (leave blank for active sheet)">
            </div>
            
            <!-- ========== UPDATE OPERATION SECTION ========== -->
            <div id="excel-export-update-section" style="display:none;">
                <hr class="my-3">
                <h6 class="fw-bold mb-3 text-primary"><i class="bi bi-key me-1"></i>Row Matching (Update Mode)</h6>
                
                <div class="mb-3">
                    <label class="form-label fw-bold">Key Column(s) <span class="text-danger">*</span></label>
                    <input type="text" class="form-control" name="keyColumns" id="excel-export-key-columns" 
                           placeholder="requirement_id, topic">
                    <small class="form-text text-muted">
                        Comma-separated column names to uniquely identify rows. Rows are matched by these columns.
                    </small>
                </div>

                <!-- AI Key Matching Option -->
                <div class="mb-3 p-3 bg-light border rounded">
                    <div class="form-check mb-2">
                        <input type="checkbox" class="form-check-input" name="useAIKeyMatching" 
                               id="excel-export-use-ai-key-matching"
                               onchange="ExcelExportNode.onAIKeyMatchingChange(this.checked)">
                        <label class="form-check-label" for="excel-export-use-ai-key-matching">
                            <strong><i class="bi bi-robot me-1"></i>Use AI-assisted key matching</strong>
                        </label>
                    </div>
                    <small class="form-text text-muted d-block mb-2">
                        Enable when key values may have minor variations (typos, word order, singular/plural). 
                        AI will match semantically similar keys to prevent duplicate rows.
                    </small>
                    
                    <div id="excel-export-ai-key-instructions-section" style="display:none;">
                        <label class="form-label small">AI Matching Instructions (optional)</label>
                        <textarea class="form-control form-control-sm" name="aiKeyMatchingInstructions" 
                                  id="excel-export-ai-key-instructions" rows="2"
                                  placeholder="e.g., Match requirements that refer to the same concept even if worded differently..."
                                  data-no-enhance="true"></textarea>
                    </div>
                </div>

                <!-- Smart Change Detection Option -->
                <div class="mb-3 p-3 bg-light border rounded">
                    <div class="form-check mb-2">
                        <input type="checkbox" class="form-check-input" name="useSmartChangeDetection" 
                               id="excel-export-use-smart-change-detection"
                               onchange="ExcelExportNode.onSmartChangeDetectionChange(this.checked)">
                        <label class="form-check-label" for="excel-export-use-smart-change-detection">
                            <strong><i class="bi bi-funnel me-1"></i>Smart Change Detection</strong>
                        </label>
                    </div>
                    <small class="form-text text-muted d-block mb-2">
                        Only update rows when the meaning has actually changed, not just the wording.
                        Reduces noise from re-extractions that produce equivalent text.
                    </small>
                    
                    <div id="excel-export-smart-change-options" style="display:none;">
                        <label class="form-label small">Detection Strictness</label>
                        <select class="form-select form-select-sm" name="smartChangeStrictness" 
                                id="excel-export-smart-change-strictness">
                            <option value="strict" selected>Strict - Preserve nuance (must vs should, all vs most)</option>
                            <option value="lenient">Lenient - Focus on facts only (numbers, dates, key requirements)</option>
                        </select>
                        <small class="form-text text-muted d-block mt-1">
                            <strong>Strict:</strong> Best for compliance/legal documents. Treats "must" vs "should" as different.<br>
                            <strong>Lenient:</strong> Best for general documentation. Only updates when facts/numbers change.
                        </small>
                    </div>
                </div>
                
                <h6 class="fw-bold mb-2 mt-4"><i class="bi bi-pencil-square me-1 text-warning"></i>Change Tracking</h6>
                
                <div class="row">
                    <div class="col-md-6">
                        <div class="form-check mb-2">
                            <input type="checkbox" class="form-check-input" name="highlightChanges" 
                                   id="excel-export-highlight-changes" checked>
                            <label class="form-check-label" for="excel-export-highlight-changes">
                                Highlight changed cells
                            </label>
                        </div>
                        
                        <div class="form-check mb-2">
                            <input type="checkbox" class="form-check-input" name="trackDeletedRows" 
                                   id="excel-export-track-deleted">
                            <label class="form-check-label" for="excel-export-track-deleted">
                                Track deleted rows
                            </label>
                        </div>
                        <small class="form-text text-muted ms-4 d-block mb-2" style="margin-top:-0.5rem;">
                            Leave unchecked for partial updates (only update matching rows)
                        </small>

                        <div class="form-check mb-2">
                            <input type="checkbox" class="form-check-input" name="addNewRecords" 
                                    id="excel-export-add-new-records" checked>
                            <label class="form-check-label" for="excel-export-add-new-records">
                                Add new records
                            </label>
                        </div>
                        <small class="form-text text-muted ms-4 d-block mb-2" style="margin-top:-0.5rem;">
                            Insert records with keys not found in existing file. Uncheck to only update existing rows.
                        </small>

                        <div class="form-check mb-2">
                            <input type="checkbox" class="form-check-input" name="addChangeTimestamp" 
                                   id="excel-export-add-timestamp" checked>
                            <label class="form-check-label" for="excel-export-add-timestamp">
                                Add "Last Updated" timestamp
                            </label>
                        </div>
                    </div>
                    
                    <div class="col-md-6">
                        <div class="mb-2">
                            <label class="form-label small">Changed Cell Color</label>
                            <div class="d-flex align-items-center">
                                <input type="color" class="form-control form-control-color me-2" 
                                       name="changeHighlightColor" id="excel-export-change-color" value="#FFFF00" style="width:50px;">
                                <small class="text-muted">Yellow</small>
                            </div>
                        </div>
                        
                        <div class="mb-2">
                            <label class="form-label small">New Row Color</label>
                            <div class="d-flex align-items-center">
                                <input type="color" class="form-control form-control-color me-2" 
                                       name="newRowColor" id="excel-export-new-row-color" value="#90EE90" style="width:50px;">
                                <small class="text-muted">Green</small>
                            </div>
                        </div>
                        
                        <div class="mb-2">
                            <label class="form-label small">Deleted Row Color</label>
                            <div class="d-flex align-items-center">
                                <input type="color" class="form-control form-control-color me-2" 
                                       name="deletedRowColor" id="excel-export-deleted-row-color" value="#FFB6C1" style="width:50px;">
                                <small class="text-muted">Pink</small>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="row mt-2">
                    <div class="col-md-6">
                        <label class="form-label small">Mark Deleted Rows As</label>
                        <select class="form-select form-select-sm" name="markDeletedAs" id="excel-export-mark-deleted-as">
                            <option value="strikethrough" selected>Strikethrough text</option>
                            <option value="color">Background color</option>
                            <option value="comment">Comment only</option>
                        </select>
                    </div>
                    
                    <div class="col-md-6">
                        <label class="form-label small">Timestamp Column Name</label>
                        <input type="text" class="form-control form-control-sm" name="timestampColumn" 
                               id="excel-export-timestamp-column" value="Last Updated">
                    </div>
                </div>
                
                <div class="mt-3">
                    <label class="form-label small">Change Log Sheet (optional)</label>
                    <input type="text" class="form-control form-control-sm" name="changeLogSheet" 
                           id="excel-export-change-log-sheet" placeholder="e.g., Change History">
                    <small class="form-text text-muted">
                        If set, creates a separate sheet logging all changes with timestamps
                    </small>
                </div>
            </div>
            <!-- ========== END UPDATE SECTION ========== -->
            
            <hr class="my-3">
            
            <!-- Column Mapping Section -->
            <div id="excel-export-mapping-section">
                <h6 class="fw-bold mb-3"><i class="bi bi-arrow-left-right me-1"></i>Column Mapping</h6>
                
                <!-- Field Names Input -->
                <div class="mb-3">
                    <label class="form-label">Field Names to Export</label>
                    <input type="text" class="form-control" id="excel-export-manual-fields" name="manualFields"
                           placeholder="topic, requirement, value, source_pages">
                    <small class="form-text text-muted">
                        Comma-separated list of field names from your data
                    </small>
                </div>
                
                <!-- Mapping Mode -->
                <div class="mb-3">
                    <label class="form-label">Mapping Mode</label>
                    <select class="form-select" name="mappingMode" id="excel-export-mapping-mode" 
                            onchange="ExcelExportNode.onMappingModeChange(this.value)">
                        <option value="ai">AI Auto-Mapping</option>
                        <option value="manual">Manual Mapping</option>
                    </select>
                </div>
                
                <!-- AI Mapping Instructions -->
                <div id="excel-export-ai-mapping-section">
                    <textarea class="form-control" name="aiMappingInstructions" id="excel-export-ai-mapping-instructions" 
                        rows="2" placeholder="Optional: Instructions for AI mapping"
                        data-no-enhance="true"></textarea>
                </div>
                
                <!-- Manual Mapping -->
                <div id="excel-export-manual-mapping-section" style="display:none;">
                    <input type="hidden" name="fieldMapping" id="excel-export-field-mapping">
                    <div id="excel-export-mapping-container" class="mb-2">
                        <p class="text-muted small">Enter field names above, then click Refresh</p>
                    </div>
                    <button type="button" class="btn btn-sm btn-outline-secondary" onclick="ExcelExportNode.refreshMappingFields()">
                        <i class="bi bi-arrow-clockwise"></i> Refresh Fields
                    </button>
                </div>
            </div>
        </div>
    `,
    defaultConfig: {
        inputVariable: '',
        flattenArray: false,
        carryForwardFields: '',
        excelOutputPath: '',
        excelOperation: 'append',
        excelTemplatePath: '',
        excelSheetName: '',
        mappingMode: 'ai',
        aiMappingInstructions: '',
        fieldMapping: null,
        manualFields: '',
        // UPDATE operation defaults
        keyColumns: '',
        highlightChanges: true,
        changeHighlightColor: '#FFFF00',
        newRowColor: '#90EE90',
        deletedRowColor: '#FFB6C1',
        trackDeletedRows: false,  // Changed to false - safer default for partial updates
        addNewRecords: true,
        markDeletedAs: 'strikethrough',
        addChangeTimestamp: true,
        timestampColumn: 'Last Updated',
        changeLogSheet: '',
        // AI Key Matching (off by default)
        useAIKeyMatching: false,
        aiKeyMatchingInstructions: '',
        // Smart Change Detection (off by default)
        useSmartChangeDetection: false,
        smartChangeStrictness: 'strict'
    }
};
