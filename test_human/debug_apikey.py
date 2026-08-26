import os, sys
os.chdir(r'C:\src\aihub-client-ai-dev')
sys.path.insert(0, r'C:\src\aihub-client-ai-dev\command_center_service')
from cc_config import AI_HUB_API_KEY
main_key = os.getenv("API_KEY", "")
print(f"CC AI_HUB_API_KEY: {AI_HUB_API_KEY[:30]}...")
print(f"Main API_KEY: {main_key[:30]}...")
print(f"Match: {AI_HUB_API_KEY == main_key}")
