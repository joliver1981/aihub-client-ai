#!/usr/bin/env python
"""
Test Archive Manager
====================

Utility for managing test output archives.

Usage:
    python archive_manager.py list              # List all archives
    python archive_manager.py show [archive]    # Show contents of an archive
    python archive_manager.py cleanup [keep]    # Remove old archives (default: keep 10)
    python archive_manager.py open [archive]    # Open archive folder in explorer
    python archive_manager.py latest            # Show the latest archive

Examples:
    python archive_manager.py list
    python archive_manager.py show 2025-01-18_14-30-45
    python archive_manager.py cleanup 5
    python archive_manager.py latest
"""

import os
import sys
import json
import shutil
import subprocess
from pathlib import Path
from datetime import datetime

# Archive directory (relative to this script)
SCRIPT_DIR = Path(__file__).parent
ARCHIVE_DIR = SCRIPT_DIR / "archives"


def list_archives():
    """List all test archives."""
    if not ARCHIVE_DIR.exists():
        print("No archives found.")
        return []
    
    archives = sorted([d for d in ARCHIVE_DIR.iterdir() if d.is_dir()], reverse=True)
    
    if not archives:
        print("No archives found.")
        return []
    
    print(f"\n{'='*60}")
    print(f"TEST ARCHIVES ({len(archives)} total)")
    print(f"{'='*60}")
    print(f"Location: {ARCHIVE_DIR}")
    print(f"{'='*60}\n")
    
    for i, archive in enumerate(archives, 1):
        # Count files in archive
        file_count = sum(1 for _ in archive.rglob("*") if _.is_file())
        
        # Get total size
        total_size = sum(f.stat().st_size for f in archive.rglob("*") if f.is_file())
        size_str = format_size(total_size)
        
        # Check for test summaries
        summaries = list(archive.rglob("test_summary.json"))
        tests_passed = sum(1 for s in summaries if json.loads(s.read_text()).get("status") == "passed")
        tests_failed = sum(1 for s in summaries if json.loads(s.read_text()).get("status") == "failed")
        
        status = ""
        if summaries:
            status = f" [✓{tests_passed}"
            if tests_failed:
                status += f" ✗{tests_failed}"
            status += "]"
        
        marker = "→ " if i == 1 else "  "
        print(f"{marker}{i}. {archive.name}  ({file_count} files, {size_str}){status}")
    
    print()
    return archives


def show_archive(archive_name: str = None):
    """Show contents of a specific archive."""
    archives = sorted([d for d in ARCHIVE_DIR.iterdir() if d.is_dir()], reverse=True) if ARCHIVE_DIR.exists() else []
    
    if not archives:
        print("No archives found.")
        return
    
    # If no name specified, use the latest
    if archive_name is None or archive_name.lower() == "latest":
        archive = archives[0]
    else:
        # Find matching archive
        matches = [a for a in archives if archive_name in a.name]
        if not matches:
            print(f"Archive not found: {archive_name}")
            print("Available archives:")
            for a in archives[:5]:
                print(f"  - {a.name}")
            return
        archive = matches[0]
    
    print(f"\n{'='*60}")
    print(f"ARCHIVE: {archive.name}")
    print(f"{'='*60}")
    print(f"Path: {archive}")
    print(f"{'='*60}\n")
    
    # List test folders
    test_folders = [d for d in archive.iterdir() if d.is_dir()]
    
    for test_folder in test_folders:
        print(f"📁 {test_folder.name}/")
        
        # Check for test summary
        summary_file = test_folder / "test_summary.json"
        if summary_file.exists():
            summary = json.loads(summary_file.read_text())
            status = "✓ PASSED" if summary.get("status") == "passed" else "✗ FAILED"
            print(f"   Status: {status}")
            if summary.get("details"):
                for key, value in summary["details"].items():
                    print(f"   {key}: {value}")
        
        # List files
        files = [f for f in test_folder.iterdir() if f.is_file() and f.name != "test_summary.json"]
        for f in files:
            size = format_size(f.stat().st_size)
            print(f"   📄 {f.name} ({size})")
        
        print()


def cleanup_archives(keep_count: int = 10):
    """Remove old archives, keeping only the most recent ones."""
    if not ARCHIVE_DIR.exists():
        print("No archives to clean.")
        return
    
    archives = sorted([d for d in ARCHIVE_DIR.iterdir() if d.is_dir()], reverse=True)
    
    if len(archives) <= keep_count:
        print(f"Archive count ({len(archives)}) <= keep count ({keep_count}), nothing to clean.")
        return
    
    to_remove = archives[keep_count:]
    
    print(f"\nWill remove {len(to_remove)} old archives:")
    for archive in to_remove:
        print(f"  🗑️ {archive.name}")
    
    response = input("\nProceed? [y/N]: ").strip().lower()
    if response != 'y':
        print("Cancelled.")
        return
    
    for archive in to_remove:
        shutil.rmtree(archive)
        print(f"Removed: {archive.name}")
    
    print(f"\n✓ Cleaned up {len(to_remove)} archives, kept {keep_count}")


def open_archive(archive_name: str = None):
    """Open archive folder in file explorer."""
    archives = sorted([d for d in ARCHIVE_DIR.iterdir() if d.is_dir()], reverse=True) if ARCHIVE_DIR.exists() else []
    
    if not archives:
        print("No archives found.")
        return
    
    if archive_name is None or archive_name.lower() == "latest":
        archive = archives[0]
    else:
        matches = [a for a in archives if archive_name in a.name]
        if not matches:
            print(f"Archive not found: {archive_name}")
            return
        archive = matches[0]
    
    print(f"Opening: {archive}")
    
    # Platform-specific open command
    if sys.platform == "win32":
        os.startfile(archive)
    elif sys.platform == "darwin":
        subprocess.run(["open", archive])
    else:
        subprocess.run(["xdg-open", archive])


def format_size(size_bytes: int) -> str:
    """Format file size for display."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    
    command = sys.argv[1].lower()
    
    if command == "list":
        list_archives()
    
    elif command == "show":
        archive_name = sys.argv[2] if len(sys.argv) > 2 else None
        show_archive(archive_name)
    
    elif command == "latest":
        show_archive("latest")
    
    elif command == "cleanup":
        keep_count = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        cleanup_archives(keep_count)
    
    elif command == "open":
        archive_name = sys.argv[2] if len(sys.argv) > 2 else "latest"
        open_archive(archive_name)
    
    else:
        print(f"Unknown command: {command}")
        print(__doc__)


if __name__ == "__main__":
    main()
