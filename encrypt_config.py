# In a file like encrypt_config.py (used before building executable)
import base64
import os
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from dotenv import load_dotenv


load_dotenv()

def generate_key(password, salt=None):
    if salt is None:
        salt = os.getenv('ENCRYPTION_SALT', 'default-salt').encode()
    """Generate an encryption key from a password"""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
    return key

def encrypt_value(value, key):
    """Encrypt a string value"""
    if value is None:
        print(f"Warning: Attempting to encrypt None value")
        return None  # or return an encrypted placeholder like "NO_KEY_PROVIDED"
    cipher = Fernet(key)
    return cipher.encrypt(value.encode()).decode()

# Your app's master password - loaded from environment
APP_SECRET = os.getenv('ENCRYPTION_SECRET', '')
encryption_key = generate_key(APP_SECRET)

# Encrypt sensitive API keys
sensitive_keys = {
    "PRIMARY": "",
    #"ANTHROPIC_API_KEY": os.getenv('ANTHROPIC_API_KEY'),
    # Add other sensitive keys,
    #"TAVILY_KEY": "",
    #"PRIMARY-EAST2": ""
}

encrypted_values = {}
for key, value in sensitive_keys.items():
    encrypted_values[key] = encrypt_value(value, encryption_key)

# Output the encrypted values to include in your config.py
print("# Copy these encrypted values to your config.py")
for key, value in encrypted_values.items():
    print(f"{key}_ENCRYPTED = '{value}'")
