#!/usr/bin/env python3
"""
Vendor Asset Downloader
Downloads all external CDN dependencies for local bundling.
Run this script from your Flask application root directory.
"""

import os
import requests
from pathlib import Path

# Base directory for vendor assets
VENDOR_DIR = Path("static/vendor")

# All assets to download: (url, local_path)
ASSETS = [
    # Bootstrap
    (
        "https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css",
        "bootstrap/css/bootstrap.min.css"
    ),
    (
        "https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/js/bootstrap.bundle.min.js",
        "bootstrap/js/bootstrap.bundle.min.js"
    ),
    
    # jQuery
    (
        "https://code.jquery.com/jquery-3.6.0.min.js",
        "jquery/jquery-3.6.0.min.js"
    ),
    
    # CodeMirror
    (
        "https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/codemirror.min.css",
        "codemirror/codemirror.min.css"
    ),
    (
        "https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/codemirror.min.js",
        "codemirror/codemirror.min.js"
    ),
    (
        "https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.2/mode/python/python.min.js",
        "codemirror/mode/python/python.min.js"
    ),
    
    # Font Awesome - CSS
    (
        "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css",
        "fontawesome/css/all.min.css"
    ),
    
    # Font Awesome - Webfonts (required for icons to display)
    (
        "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/webfonts/fa-solid-900.woff2",
        "fontawesome/webfonts/fa-solid-900.woff2"
    ),
    (
        "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/webfonts/fa-solid-900.woff",
        "fontawesome/webfonts/fa-solid-900.woff"
    ),
    (
        "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/webfonts/fa-solid-900.ttf",
        "fontawesome/webfonts/fa-solid-900.ttf"
    ),
    (
        "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/webfonts/fa-regular-400.woff2",
        "fontawesome/webfonts/fa-regular-400.woff2"
    ),
    (
        "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/webfonts/fa-regular-400.woff",
        "fontawesome/webfonts/fa-regular-400.woff"
    ),
    (
        "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/webfonts/fa-regular-400.ttf",
        "fontawesome/webfonts/fa-regular-400.ttf"
    ),
    (
        "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/webfonts/fa-brands-400.woff2",
        "fontawesome/webfonts/fa-brands-400.woff2"
    ),
    (
        "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/webfonts/fa-brands-400.woff",
        "fontawesome/webfonts/fa-brands-400.woff"
    ),
    (
        "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/webfonts/fa-brands-400.ttf",
        "fontawesome/webfonts/fa-brands-400.ttf"
    ),
    
    # Moment.js
    (
        "https://cdnjs.cloudflare.com/ajax/libs/moment.js/2.29.1/moment.min.js",
        "moment/moment.min.js"
    ),
    
    # Tempus Dominus (Bootstrap 4 Datepicker)
    (
        "https://cdnjs.cloudflare.com/ajax/libs/tempusdominus-bootstrap-4/5.39.0/css/tempusdominus-bootstrap-4.min.css",
        "tempusdominus/css/tempusdominus-bootstrap-4.min.css"
    ),
    (
        "https://cdnjs.cloudflare.com/ajax/libs/tempusdominus-bootstrap-4/5.39.0/js/tempusdominus-bootstrap-4.min.js",
        "tempusdominus/js/tempusdominus-bootstrap-4.min.js"
    ),
]


def download_file(url: str, local_path: Path) -> bool:
    """Download a file from URL to local path."""
    try:
        print(f"  Downloading: {url}")
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        # Create parent directories if needed
        local_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write file
        with open(local_path, 'wb') as f:
            f.write(response.content)
        
        print(f"  ✓ Saved to: {local_path}")
        return True
        
    except requests.RequestException as e:
        print(f"  ✗ Failed: {e}")
        return False


def fix_fontawesome_css():
    """
    Fix Font Awesome CSS to use correct relative paths.
    The CDN CSS references ../webfonts/ but our structure needs adjustment.
    """
    css_path = VENDOR_DIR / "fontawesome/css/all.min.css"
    
    if not css_path.exists():
        print("  ⚠ Font Awesome CSS not found, skipping path fix")
        return
    
    print("  Fixing Font Awesome CSS webfont paths...")
    
    with open(css_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # The CSS uses ../webfonts/ which should work with our structure
    # but let's verify and ensure consistency
    # Original: ../webfonts/fa-solid-900.woff2
    # Our structure: fontawesome/css/all.min.css and fontawesome/webfonts/
    # So ../webfonts/ should resolve correctly
    
    # No changes needed if structure matches, but let's ensure no CDN URLs remain
    if 'cdnjs.cloudflare.com' in content:
        print("  ⚠ Warning: CDN URLs found in CSS, may need manual fixes")
    else:
        print("  ✓ Font Awesome CSS paths look correct")


def main():
    print("=" * 60)
    print("Vendor Asset Downloader")
    print("=" * 60)
    print(f"\nTarget directory: {VENDOR_DIR.absolute()}\n")
    
    # Create vendor directory
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    
    success_count = 0
    fail_count = 0
    
    for url, local_path in ASSETS:
        full_path = VENDOR_DIR / local_path
        if download_file(url, full_path):
            success_count += 1
        else:
            fail_count += 1
    
    print("\n" + "=" * 60)
    
    # Fix Font Awesome paths
    fix_fontawesome_css()
    
    print("\n" + "=" * 60)
    print(f"Download complete!")
    print(f"  ✓ Success: {success_count}")
    print(f"  ✗ Failed:  {fail_count}")
    print("=" * 60)
    
    if fail_count == 0:
        print("\n✓ All assets downloaded successfully!")
        print("\nNext steps:")
        print("1. Update your base.html to use local paths (see base_html_local.html)")
        print("2. Test your application")
        print("3. Commit the vendor folder to your repository")
    else:
        print("\n⚠ Some downloads failed. Check your internet connection and retry.")


if __name__ == "__main__":
    main()
