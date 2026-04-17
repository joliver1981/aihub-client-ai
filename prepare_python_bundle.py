"""
Hybrid Environment Manager - Bundles select packages and installs others via pip
Uses requirements.txt from the python-bundle folder
"""

import os
import sys
import shutil
import subprocess
import json
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import tempfile
from agent_environments.environment_manager import HybridPythonBundler, HybridPythonBundlerLite


# Example usage
def create_production_bundle():
    """
    Create your production bundle with bundled packages and requirements.txt
    """
    
    # Modified packages to bundle
    bundled_packages = [
        'langchain',      # modified version
        'openai',         # modified version
    ]
    
    # Option 1: Use existing requirements.txt file
    bundler = HybridPythonBundlerLite(output_dir="./agent_environments/python-bundle")
    success = bundler.create_hybrid_bundle(
        bundled_packages=bundled_packages,
        requirements_file="./agent_environments/python-bundle-requirements/requirements.txt"
    )
    
    return success

if __name__ == "__main__":
    create_production_bundle()