# Fix hardcoded light-theme colors in inline <style> blocks
# Replaces with CSS variables (with fallbacks for compatibility)

$templatesDir = "C:\src\aihub-client-ai-dev\templates"

# Skip already-fixed or special pages
$skip = @('login.html','approvals.html','landing.html','initial_setup.html','document_view.html')

$files = Get-ChildItem "$templatesDir\*.html" | Where-Object { $skip -notcontains $_.Name }

# Also check document_processor subdir
$dpFiles = Get-ChildItem "$templatesDir\document_processor\*.html" -ErrorAction SilentlyContinue
if ($dpFiles) { $files = @($files) + @($dpFiles) }

$totalChanges = 0

foreach ($file in $files) {
    $content = Get-Content $file.FullName -Raw
    $original = $content
    
    # --- Background replacements ---
    # background: white / background: #fff (but NOT background: #fff3cd or similar)
    $content = $content -replace '(background:\s*)white(\s*;)', '$1var(--bg-card, white)$2'
    $content = $content -replace '(background:\s*)#fff(\s*;)', '$1var(--bg-card, #fff)$2'
    $content = $content -replace '(background-color:\s*)white(\s*;)', '$1var(--bg-card, white)$2'
    
    # background-color: #f8f9fa
    $content = $content -replace '(background(?:-color)?:\s*)#f8f9fa(\s*;)', '$1var(--bg-elevated, #f8f9fa)$2'
    
    # --- Text color replacements ---
    # color: #333 (but not #333333 or longer)  
    $content = $content -replace '((?<!background-)color:\s*)#333(\s*;)', '$1var(--text-primary, #333)$2'
    
    # color: #666
    $content = $content -replace '((?<!background-)color:\s*)#666(\s*;)', '$1var(--text-secondary, #666)$2'
    
    # color: #6c757d
    $content = $content -replace '((?<!background-)color:\s*)#6c757d(\s*;)', '$1var(--text-muted, #6c757d)$2'
    
    # --- Border replacements ---
    # border colors #dee2e6
    $content = $content -replace '#dee2e6', 'var(--border-subtle, #dee2e6)'
    
    # border colors #ddd
    $content = $content -replace '(border[^:]*:\s*[^;]*?)#ddd', '$1var(--border-subtle, #ddd)'
    
    # --- Box shadow muting ---
    # Don't change box-shadows, the theme CSS handles those
    
    if ($content -ne $original) {
        Set-Content $file.FullName -Value $content -NoNewline
        $changes = 0
        $origLines = $original -split "`n"
        $newLines = $content -split "`n"
        for ($i = 0; $i -lt [Math]::Max($origLines.Count, $newLines.Count); $i++) {
            if ($i -ge $origLines.Count -or $i -ge $newLines.Count -or $origLines[$i] -ne $newLines[$i]) {
                $changes++
            }
        }
        Write-Output "$($file.Name): $changes line(s) changed"
        $totalChanges += $changes
    }
}

Write-Output "`nTotal: $totalChanges line(s) changed across all files"
