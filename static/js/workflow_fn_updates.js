// Fix for context menu not working after "Create New Workflow"
// Add this code to your workflow.js or include it as a separate script

// Store the original functions
const originalClearWorkflow = window.clearWorkflow;
const originalCreateNewWorkflow = window.createNewWorkflow;

// Override clearWorkflow to properly re-initialize jsPlumb
window.clearWorkflow = function() {
    const canvas = document.getElementById('workflow-canvas');
    if (canvas) {
        canvas.innerHTML = '';
        
        // Instead of just resetting, we need to properly re-initialize jsPlumb
        jsPlumbInstance.reset();
        
        // IMPORTANT: Re-bind the connection event handler that was lost
        jsPlumbInstance.bind('connection', function(info) {
            console.log('Re-binding connection event after clearWorkflow');
            
            // Set default connection type to pass
            setArrowType('pass', info.connection);
            
            // Store the original anchors in the connection data
            const sourceAnchor = info.connection.endpoints[0].anchor.type || "Right";
            const targetAnchor = info.connection.endpoints[1].anchor.type || "Left";
            info.connection.setData({
                type: 'pass',
                sourceAnchor: sourceAnchor,
                targetAnchor: targetAnchor
            });
            
            // Bind context menu to the connection's canvas element
            setTimeout(() => {
                if (info.connection && info.connection.canvas) {
                    bindContextMenuToConnectionCanvas(info.connection);
                }
            }, 100);
        });
        
        startNode = null;
        nodeConfigs.clear();
    }
};

// Helper function to bind context menu to a connection's canvas
function bindContextMenuToConnectionCanvas(connection) {
    if (!connection || !connection.canvas) return;
    
    // Remove any existing handler to avoid duplicates
    if (connection.canvas._contextMenuHandler) {
        connection.canvas.removeEventListener('contextmenu', connection.canvas._contextMenuHandler);
    }
    
    // Create the handler
    const handler = function(e) {
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
    };
    
    // Store handler reference
    connection.canvas._contextMenuHandler = handler;
    
    // Add the event listener
    connection.canvas.addEventListener('contextmenu', handler);
}

// Override createNewWorkflow to ensure proper re-initialization
window.createNewWorkflow = function(showConfirm=true) {
    // Confirm with the user if there's an existing workflow open
    if (currentWorkflowName && showConfirm) {
        if (!confirm('Are you sure you want to create a new workflow? Any unsaved changes will be lost.')) {
            return;
        }
    }
    
    // Clear the canvas and reset all state
    clearWorkflow();
    
    // Re-initialize context menus with ALL necessary bindings
    // We need to completely re-setup the context menu system
    setupContextMenusComplete();
    
    // Reset current workflow indicators
    currentWorkflowName = null;
    updateCurrentWorkflowDisplay();
    
    // Clear variables
    workflowVariableDefinitions = {};
    workflowVariables = {};
    
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
};

// Complete context menu setup that handles all cases
function setupContextMenusComplete() {
    const arrowMenu = document.getElementById('arrow-context-menu');
    const nodeMenu = document.getElementById('node-context-menu');
    
    // Remove any existing click handlers to avoid duplicates
    const newClickHandler = function(e) {
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
            if (arrowMenu) arrowMenu.style.display = 'none';
            if (nodeMenu) nodeMenu.style.display = 'none';
            selectedConnection = null;
            selectedNode = null;
        }
    };
    
    // Remove old handlers and add new one
    document.removeEventListener('click', window._contextMenuClickHandler);
    window._contextMenuClickHandler = newClickHandler;
    document.addEventListener('click', newClickHandler);
    
    // Remove any existing contextmenu handlers to avoid duplicates
    const newContextMenuHandler = function(e) {
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
            
            if (nodeMenu) {
                nodeMenu.style.display = 'block';
                nodeMenu.style.left = e.pageX + 'px';
                nodeMenu.style.top = e.pageY + 'px';
            }
        }
    };
    
    // Remove old handler and add new one
    document.removeEventListener('contextmenu', window._nodeContextMenuHandler);
    window._nodeContextMenuHandler = newContextMenuHandler;
    document.addEventListener('contextmenu', newContextMenuHandler);
    
    // CRITICAL: Re-bind jsPlumb connection event
    // First, unbind any existing handlers to avoid duplicates
    jsPlumbInstance.unbind('connection');
    
    // Now bind fresh
    jsPlumbInstance.bind('connection', function(info) {
        console.log('Connection event fired in setupContextMenusComplete');

        // Set default connection type to pass
        setArrowType('pass', info.connection);

        // Store the original anchors in the connection data
        const sourceAnchor = info.connection.endpoints[0].anchor.type || "Right";
        const targetAnchor = info.connection.endpoints[1].anchor.type || "Left";
        info.connection.setData({
            type: 'pass',
            sourceAnchor: sourceAnchor,
            targetAnchor: targetAnchor
        });

        // Bind context menu with a delay to ensure canvas is ready
        setTimeout(() => {
            bindContextMenuToConnectionCanvas(info.connection);
        }, 100);

        // Trigger debounced validation so duplicate-slot / end-loop-back-edge
        // warnings surface immediately. This file's bind clobbers the one
        // workflow.js installed, so the hook must live here too.
        if (window.requestWorkflowValidation) window.requestWorkflowValidation();
    });

    // Re-validate when a connection is removed so warning rings clear when
    // the duplicate-slot is resolved.
    jsPlumbInstance.unbind('connectionDetached');
    jsPlumbInstance.bind('connectionDetached', function() {
        if (window.requestWorkflowValidation) window.requestWorkflowValidation();
    });
    
    // Bind context menu to any existing connections
    jsPlumbInstance.getAllConnections().forEach(connection => {
        bindContextMenuToConnectionCanvas(connection);
    });
    
    console.log('Context menus completely re-initialized');
}

// Also fix the loadWorkflow function to ensure context menus work after loading
const originalLoadWorkflow = window.loadWorkflow;
window.loadWorkflow = function(workflow) {
    // Call the original function
    if (originalLoadWorkflow) {
        originalLoadWorkflow.call(this, workflow);
    }
    
    // After loading, ensure all connections have context menus
    setTimeout(() => {
        jsPlumbInstance.getAllConnections().forEach(connection => {
            bindContextMenuToConnectionCanvas(connection);
        });
        console.log('Context menus bound to all loaded connections');
    }, 500);
};

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    // Ensure context menus are properly set up initially
    setTimeout(() => {
        if (typeof setupContextMenusComplete === 'function') {
            setupContextMenusComplete();
        }
    }, 100);
});

console.log('Workflow context menu fix loaded - Create New Workflow should now work properly');
