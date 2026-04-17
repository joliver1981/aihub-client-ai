/**
 * Page Context: Custom Tool Builder
 * 
 * Provides real-time context about the custom tool builder page.
 * Add this script to custom_tool.html
 * 
 * Extracts:
 * - Package and tool configuration
 * - Module dependencies
 * - Parameter definitions
 * - Code editor content and analysis
 */

window.assistantPageContext = {
    page: 'custom_tool',
    pageName: 'Custom Tool Builder',
    
    // Dynamic context - called each time user sends a message
    getPageData: function() {
        // Get current form state
        var packageName = document.getElementById('package')?.value || '';
        var toolName = document.getElementById('name')?.value || '';
        var description = document.getElementById('description')?.value || '';
        var outputType = document.getElementById('output')?.value || 'str';
        
        // Get modules list
        var moduleItems = document.querySelectorAll('#itemsList .list-group-item');
        var modules = Array.from(moduleItems).map(function(item) {
            return item.textContent.replace('×', '').trim();
        });
        
        // Get parameters list
        var paramItems = document.querySelectorAll('#paramsList .list-group-item');
        var parameters = Array.from(paramItems).map(function(item) {
            var text = item.textContent;
            return text.replace('×', '').trim();
        });
        
        // Get code editor content using multiple fallback methods
        var code = getCodeFromEditor();
        
        // Code analysis
        var codeLines = code.split('\n');
        var codeStats = {
            lineCount: codeLines.length,
            hasReturn: code.includes('return '),
            usesDb: code.includes('db.') || code.includes('cursor.') || code.includes('pyodbc'),
            hasImports: code.includes('import '),
            hasTryExcept: code.includes('try:') && code.includes('except'),
            hasComments: code.includes('#'),
            usesParameters: parameters.some(function(param) {
                var paramName = param.split(':')[0].trim();
                return paramName && code.includes(paramName);
            })
        };
        
        // Generate contextual hints
        var hints = getAssistantHints(codeStats, parameters, modules, code);
        
        return {
            // Current package info
            currentPackage: packageName,
            isNewPackage: packageName === '' || packageName === 'new',
            
            // Tool configuration
            toolName: toolName,
            description: description,
            outputType: outputType,
            
            // Components
            moduleCount: modules.length,
            modules: modules.slice(0, 10),  // Limit to first 10
            parameterCount: parameters.length,
            parameters: parameters.slice(0, 10),  // Limit to first 10
            
            // Code content (truncated to avoid huge payloads)
            code: code.length > 4000 ? code.substring(0, 4000) + '\n... [truncated]' : code,
            
            // Code statistics
            codeLineCount: codeStats.lineCount,
            codeHasReturn: codeStats.hasReturn,
            codeUsesDatabase: codeStats.usesDb,
            codeHasImports: codeStats.hasImports,
            codeHasTryExcept: codeStats.hasTryExcept,
            codeHasComments: codeStats.hasComments,
            codeUsesParameters: codeStats.usesParameters,
            
            // Hints for the assistant
            hints: hints,
            
            // Available actions
            availableActions: getAvailableActions(toolName, code, parameters)
        };
    }
};

/**
 * Extract code from CodeMirror editor using multiple fallback methods
 */
function getCodeFromEditor() {
    var code = '';
    
    // Method 1: Try window.codeEditor (if explicitly exposed)
    if (window.codeEditor && typeof window.codeEditor.getValue === 'function') {
        try {
            code = window.codeEditor.getValue();
            if (code) {
                console.log('Code extracted via window.codeEditor');
                return code;
            }
        } catch (e) {
            console.log('window.codeEditor.getValue() failed:', e);
        }
    }
    
    // Method 2: Try window.editor (in case it's global)
    if (window.editor && typeof window.editor.getValue === 'function') {
        try {
            code = window.editor.getValue();
            if (code) {
                console.log('Code extracted via window.editor');
                return code;
            }
        } catch (e) {
            console.log('window.editor.getValue() failed:', e);
        }
    }
    
    // Method 3: Find CodeMirror instance attached to the textarea
    var textarea = document.getElementById('code');
    if (textarea && textarea.CodeMirror && typeof textarea.CodeMirror.getValue === 'function') {
        try {
            code = textarea.CodeMirror.getValue();
            if (code) {
                console.log('Code extracted via textarea.CodeMirror');
                return code;
            }
        } catch (e) {
            console.log('textarea.CodeMirror.getValue() failed:', e);
        }
    }
    
    // Method 4: Extract directly from CodeMirror DOM structure
    // CodeMirror renders code as: .CodeMirror-code > div > pre.CodeMirror-line > span
    var cmCode = document.querySelector('.CodeMirror-code');
    if (cmCode) {
        try {
            var lines = cmCode.querySelectorAll('.CodeMirror-line');
            if (lines.length > 0) {
                var codeLines = [];
                lines.forEach(function(line) {
                    // Get text content, handling special empty line markers
                    var lineText = line.textContent || '';
                    // CodeMirror uses special Unicode char for empty lines
                    if (lineText === '\u200B' || lineText === '​') {
                        lineText = '';
                    }
                    codeLines.push(lineText);
                });
                code = codeLines.join('\n');
                if (code.trim()) {
                    console.log('Code extracted via CodeMirror DOM (' + lines.length + ' lines)');
                    return code;
                }
            }
        } catch (e) {
            console.log('CodeMirror DOM extraction failed:', e);
        }
    }
    
    // Method 5: Fallback to original textarea value
    if (textarea) {
        code = textarea.value || '';
        if (code) {
            console.log('Code extracted via textarea fallback');
            return code;
        }
    }
    
    console.log('No code could be extracted from editor');
    return '';
}

// Helper function to generate contextual hints
function getAssistantHints(codeStats, parameters, modules, code) {
    var hints = [];
    
    if (!codeStats.hasReturn && codeStats.lineCount > 1) {
        hints.push('Code may be missing a return statement');
    }
    
    if (codeStats.usesDb && !modules.includes('pyodbc') && !modules.includes('db')) {
        hints.push('Code uses database but pyodbc module may not be added');
    }
    
    if (parameters.length === 0) {
        hints.push('No parameters defined - tool will have no inputs');
    }
    
    if (codeStats.lineCount < 3 && code.trim().length > 0) {
        hints.push('Code is very short - may be incomplete');
    }
    
    if (!codeStats.hasTryExcept && codeStats.usesDb) {
        hints.push('Database code without try/except - consider adding error handling');
    }
    
    if (parameters.length > 0 && !codeStats.usesParameters) {
        hints.push('Parameters defined but may not be used in code');
    }
    
    // Check for common issues
    if (code.includes('print(') && !code.includes('return ')) {
        hints.push('Using print() but no return - tool output comes from return statement');
    }
    
    return hints;
}

// Helper function to determine available actions
function getAvailableActions(toolName, code, parameters) {
    var actions = [];
    
    if (!toolName) {
        actions.push('Enter a tool name');
    }
    
    if (code.trim().length === 0) {
        actions.push('Write the tool code');
    } else {
        actions.push('Review and edit code');
    }
    
    if (parameters.length === 0) {
        actions.push('Add input parameters');
    } else {
        actions.push('Modify parameters');
    }
    
    actions.push('Add required modules');
    actions.push('Test the tool');
    actions.push('Save the tool');
    
    return actions;
}

console.log('Custom Tool Builder context loaded');
