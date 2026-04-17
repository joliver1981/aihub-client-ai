"""
Configuration for Agent Environments module
"""

import os

class EnvironmentConfig:
    # Feature flags
    ENABLED = os.getenv('AGENT_ENVIRONMENTS_ENABLED', 'false').lower() == 'true'
    
    # Paths - environments stored per tenant
    BASE_PATH = os.getenv('AGENT_ENVIRONMENTS_PATH', os.path.join(os.getenv('APP_ROOT', os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'agent_environments'))
    
    # Limits (for premium tiers)
    MAX_ENVIRONMENTS = {
        'free': 2,
        'basic': 5,
        'pro': 25,
        'enterprise': -1  # Unlimited
    }
    
    MAX_PACKAGES_PER_ENV = 50
    MAX_ENV_SIZE_MB = 500
    
    # Security - Curated package lists
    ALLOWED_PACKAGES = [
        'pandas', 'numpy', 'requests', 'beautifulsoup4',
        'matplotlib', 'seaborn', 'plotly', 'scikit-learn',
        'openpyxl', 'xlsxwriter', 'lxml', 'pillow',
        'python-dotenv', 'pyyaml', 'jsonschema', 'jinja2',
        'sqlalchemy', 'pymongo', 'redis', 'celery',
        'flask', 'fastapi', 'streamlit', 'dash',
        'tensorflow', 'torch', 'transformers', 'opencv-python',
        'pyodbc'
    ]
    
    BLOCKED_PACKAGES = [
        'subprocess32', 'os-system', 'eval-literal',
        'pyautogui', 'keyboard', 'mouse'  # Prevent UI automation
    ]
    
    # Templates for quick setup
    DEFAULT_TEMPLATES = {
        'data_analysis': {
            'name': 'Data Analysis',
            'packages': ['pandas', 'numpy', 'matplotlib', 'openpyxl'],
            'description': 'Essential packages for data analysis tasks'
        },
        'web_scraping': {
            'name': 'Web Scraping',
            'packages': ['requests', 'beautifulsoup4', 'lxml'],
            'description': 'Tools for web scraping and HTML parsing'
        },
        'machine_learning': {
            'name': 'Machine Learning',
            'packages': ['scikit-learn', 'pandas', 'numpy', 'matplotlib'],
            'description': 'Basic machine learning environment'
        },
        'api_development': {
            'name': 'API Development',
            'packages': ['flask', 'requests', 'python-dotenv'],
            'description': 'Tools for building and testing APIs'
        }
    }
    
    # Environment loading strategy
    LAZY_LOADING = True  # Don't load environments until needed
    CACHE_TIMEOUT_MINUTES = 30  # How long to keep environments in memory
    MAX_CONCURRENT_ENVIRONMENTS = 10  # Max environments loaded at once per tenant
    RESTRICT_PACKAGES = False
