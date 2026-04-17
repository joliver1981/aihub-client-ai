"""
Application configuration with environment-based overrides.
Handles both normal and PyInstaller-frozen execution contexts.
"""
import os
import sys


def _get_base_dir():
    """Resolve base directory for both frozen (PyInstaller) and normal execution."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _get_internal_dir():
    """Resolve internal resource directory (templates, static) for frozen apps."""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Config:
    """Application configuration."""

    BASE_DIR = _get_base_dir()
    INTERNAL_DIR = _get_internal_dir()

    # Server
    HOST = os.getenv('FC_HOST', '0.0.0.0')
    PORT = int(os.getenv('FC_PORT', 5005))
    THREADS = int(os.getenv('FC_THREADS', 4))
    DEBUG = os.getenv('FC_DEBUG', 'false').lower() == 'true'

    # Paths - writable data goes to BASE_DIR, bundled resources use INTERNAL_DIR
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    MODEL_FOLDER = os.path.join(BASE_DIR, 'models')
    LOG_DIR = os.path.join(BASE_DIR, 'logs')
    TEMPLATE_FOLDER = os.path.join(INTERNAL_DIR, 'templates')
    STATIC_FOLDER = os.path.join(INTERNAL_DIR, 'static')

    # Upload limits
    MAX_CONTENT_LENGTH = int(os.getenv('FC_MAX_UPLOAD_MB', 500)) * 1024 * 1024

    # Training defaults
    DEFAULT_TEST_SIZE = 0.2
    DEFAULT_RANDOM_STATE = 42

    # DNN defaults
    DNN_EPOCHS = int(os.getenv('FC_DNN_EPOCHS', 100))
    DNN_BATCH_SIZE = int(os.getenv('FC_DNN_BATCH_SIZE', 32))
    DNN_DROPOUT = float(os.getenv('FC_DNN_DROPOUT', 0.2))
    DNN_LAYER_1 = int(os.getenv('FC_DNN_LAYER_1', 128))
    DNN_LAYER_2 = int(os.getenv('FC_DNN_LAYER_2', 64))
    DNN_PATIENCE = int(os.getenv('FC_DNN_PATIENCE', 10))
    DNN_USE_KFOLD = os.getenv('FC_DNN_USE_KFOLD', 'true').lower() == 'true'
    DNN_NUM_FOLDS = int(os.getenv('FC_DNN_NUM_FOLDS', 5))

    # Feature analysis
    CUMULATIVE_IMPORTANCE_THRESHOLD = float(os.getenv('FC_CUMULATIVE_IMPORTANCE', 0.99))
    FEATURE_IMPORTANCE_ITERATIONS = int(os.getenv('FC_FEATURE_IMPORTANCE_ITER', 10))

    # Confidence
    MC_DROPOUT_ITERATIONS = int(os.getenv('FC_MC_DROPOUT_ITER', 100))
    CONFIDENCE_LEVEL = float(os.getenv('FC_CONFIDENCE_LEVEL', 0.80))

    @classmethod
    def ensure_directories(cls):
        """Create required directories if they don't exist."""
        for d in [cls.UPLOAD_FOLDER, cls.MODEL_FOLDER, cls.LOG_DIR]:
            os.makedirs(d, exist_ok=True)


def get_config():
    """Return the application config."""
    return Config
