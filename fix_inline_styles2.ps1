# Second pass: borders, additional backgrounds
$templatesDir = "C:\src\aihub-client-ai-dev\templates"
$skip = @('login.html','approvals.html','landing.html','initial_setup.html','document_view.html')
$files = Get-ChildItem "$templatesDir\*.html" | Where-Object { $skip -notcontains $_.Name }

$totalChanges = 0

foreach ($file in $files) {
    $content = Get-Content $file.FullName -Raw
    $original = $content
    
    # Border colors
    $content = $content -replace '#e9ecef', 'var(--border-subtle, #e9ecef)'
    # #eee in border contexts only (careful not to break hex colors like #eee123)
    $content = $content -replace '(border[^:]*:\s*[^;]*?)#eee\b', '$1var(--border-subtle, #eee)'
    
    # Neutral light backgrounds (not status colors)
    $content = $content -replace '(background(?:-color)?:\s*)#f8f9ff(\s*;)', '$1var(--bg-elevated, #f8f9ff)$2'
    $content = $content -replace '(background(?:-color)?:\s*)#fafbff(\s*;)', '$1var(--bg-elevated, #fafbff)$2'
    $content = $content -replace '(background(?:-color)?:\s*)#f9f9f9(\s*;)', '$1var(--bg-elevated, #f9f9f9)$2'
    $content = $content -replace '(background(?:-color)?:\s*)#f3f3f3(\s*;)', '$1var(--bg-elevated, #f3f3f3)$2'
    $content = $content -replace '(background(?:-color)?:\s*)#f1f1f1(\s*;)', '$1var(--bg-elevated, #f1f1f1)$2'
    $content = $content -replace '(background(?:-color)?:\s*)#f0f0f0(\s*;)', '$1var(--bg-elevated, #f0f0f0)$2'
    $content = $content -replace '(background(?:-color)?:\s*)#f1f3f5(\s*;)', '$1var(--bg-elevated, #f1f3f5)$2'
    $content = $content -replace '(background(?:-color)?:\s*)#fefefe(\s*;)', '$1var(--bg-card, #fefefe)$2'
    
    # Status alert backgrounds - make them semi-transparent for dark mode
    $content = $content -replace '(background(?:-color)?:\s*)#f8d7da(\s*;)', '$1rgba(251, 113, 133, 0.15)$2'
    $content = $content -replace '(background(?:-color)?:\s*)#fff3cd(\s*;)', '$1rgba(251, 191, 36, 0.15)$2'
    $content = $content -replace '(background(?:-color)?:\s*)#f0fff4(\s*;)', '$1rgba(52, 211, 153, 0.15)$2'
    $content = $content -replace '(background(?:-color)?:\s*)#fee2e2(\s*)', '$1rgba(251, 113, 133, 0.15)$2'
    $content = $content -replace '(background(?:-color)?:\s*)#fff5f5(\s*;)', '$1rgba(251, 113, 133, 0.1)$2'
    
    # Status text colors that reference dark-on-light combos
    $content = $content -replace '((?<!background-)color:\s*)#856404(\s*;)', '$1#fbbf24$2'
    $content = $content -replace '((?<!background-)color:\s*)#721c24(\s*;)', '$1#fb7185$2'
    $content = $content -replace '((?<!background-)color:\s*)#155724(\s*;)', '$1#34d399$2'
    
    # General text colors
    $content = $content -replace '((?<!background-)color:\s*)#212529(\s*;)', '$1var(--text-primary, #212529)$2'
    
    if ($content -ne $original) {
        Set-Content $file.FullName -Value $content -NoNewline
        $changes = 0
        $origLines = $original -split "`n"
        $newLines = $content -split "`n"
        for ($i = 0; $i -lt [Math]::Max($origLines.Count, $newLines.Count); $i++) {
            if ($i -ge $origLines.Count -or $i -ge $newLines.Count -or $origLines[$i] -ne $newLines[$i]) { $changes++ }
        }
        Write-Output "$($file.Name): $changes line(s) changed"
        $totalChanges += $changes
    }
}
Write-Output "`nTotal: $totalChanges line(s) changed"
