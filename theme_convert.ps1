# Theme conversion script for AI Hub pages
# Adds doc-page wrapper, theme CSS link, and theme toggle to pages

param(
    [string]$TemplatesDir = "C:\src\aihub-client-ai-dev\templates"
)

$themeToggleScript = @'

<!-- Theme Toggle Script -->
<script>
function toggleDocTheme() {
    var page = document.getElementById('docPage');
    var icon = document.getElementById('docThemeIcon');
    var label = document.getElementById('docThemeLabel');
    page.classList.toggle('light-mode');
    var isLight = page.classList.contains('light-mode');
    icon.className = isLight ? 'fas fa-sun' : 'fas fa-moon';
    label.textContent = isLight ? 'Light' : 'Dark';
    localStorage.setItem('docPageTheme', isLight ? 'light' : 'dark');
}
(function() {
    var saved = localStorage.getItem('docPageTheme');
    if (saved === 'light') {
        var page = document.getElementById('docPage');
        if (page) {
            page.classList.add('light-mode');
            var icon = document.getElementById('docThemeIcon');
            var label = document.getElementById('docThemeLabel');
            if (icon) icon.className = 'fas fa-sun';
            if (label) label.textContent = 'Light';
        }
    }
})();
</script>
'@

$pages = @(
    'assistants.html',
    'data_assistants.html',
    'custom_agent_enhanced.html',
    'custom_data_agent.html',
    'jobs.html',
    'approvals.html',
    'connections.html',
    'data_dictionary.html',
    'local_secrets.html',
    'llm_unit_test.html',
    'custom_tool.html',
    'agent_environment_assignments.html',
    'mcp_servers.html',
    'integrations.html',
    'users.html',
    'groups.html',
    'email_processing_history.html',
    'api_keys_config.html',
    'admin\identity_settings.html',
    'system_logs.html',
    'feedback_analysis.html',
    'user_preferences.html',
    'telemetry_settings.html'
)

foreach ($page in $pages) {
    $filePath = Join-Path $TemplatesDir $page
    if (-not (Test-Path $filePath)) {
        Write-Output "SKIP (not found): $page"
        continue
    }

    $content = Get-Content $filePath -Raw

    # Skip if already converted
    if ($content -match 'doc-pages-theme\.css|dashboard-theme\.css') {
        Write-Output "SKIP (already themed): $page"
        continue
    }

    # 1. Add CSS link after {% block content %}
    $cssLink = @'
{% block content %}
<!-- Document Pages Dark Theme -->
<link rel="stylesheet" href="/static/css/doc-pages-theme.css">

<div class="doc-page" id="docPage">
'@
    $content = $content -replace '{% block content %}', $cssLink

    # 2. Replace compact-header with doc-page-header
    # Match the compact-header div pattern
    $compactHeaderPattern = '(?s)<div class="compact-header[^"]*"[^>]*>\s*<h4[^>]*>(<i[^>]*></i>\s*)?([^<]+)</h4>\s*(?:<[^/].*?)?</div>'
    
    if ($content -match $compactHeaderPattern) {
        $iconMatch = if ($Matches[1]) { $Matches[1].Trim() } else { '<i class="fas fa-cog"></i>' }
        $titleText = $Matches[2].Trim()
        
        $newHeader = @"
<div class="doc-page-header">
        <div>
            <h4 class="doc-page-header-title">$iconMatch $titleText</h4>
        </div>
        <div class="doc-page-header-actions">
            <button class="doc-theme-toggle" onclick="toggleDocTheme()" title="Toggle theme">
                <i class="fas fa-moon" id="docThemeIcon"></i> <span id="docThemeLabel">Dark</span>
            </button>
        </div>
    </div>
"@
        # Simple replacement - just replace the compact-header div
        $content = $content -replace '<div class="compact-header d-flex justify-content-between align-items-center">', '<div class="doc-page-header">'
        # Also handle cases without d-flex
        $content = $content -replace '<div class="compact-header">', '<div class="doc-page-header">'
    } else {
        # No compact header - add a theme toggle button after the doc-page wrapper
        $content = $content -replace '(<div class="doc-page" id="docPage">)', @'
$1
<div style="position:fixed;bottom:20px;right:20px;z-index:100;">
    <button class="doc-theme-toggle" onclick="toggleDocTheme()" title="Toggle theme">
        <i class="fas fa-moon" id="docThemeIcon"></i> <span id="docThemeLabel">Dark</span>
    </button>
</div>
'@
    }

    # 3. Replace bg-primary card headers
    $content = $content -replace 'class="card-header bg-primary text-white"', 'class="card-header doc-card-header"'
    $content = $content -replace 'class="card-header bg-info text-white"', 'class="card-header doc-card-header"'
    
    # 4. Replace bg-light on card headers  
    $content = $content -replace 'class="card-header bg-light"', 'class="card-header doc-card-header"'

    # 5. Add closing wrappers and theme toggle before {% endblock %}
    $closingBlock = @"
$themeToggleScript
</div><!-- /.doc-page -->
{% endblock %}
"@
    $content = $content -replace '{% endblock %}', $closingBlock

    # Write the file
    Set-Content $filePath $content -NoNewline
    Write-Output "CONVERTED: $page"
}

Write-Output "`nDone! Pages converted."
