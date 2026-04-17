# Command Center Agent — Full Build Instructions

Wire up the Command Center Agent — a FastAPI+LangGraph microservice at `command_center_service/` (port 5091).

## Context
- Project root: C:\src\aihub-client-ai-dev
- Main Flask app: app.py (port 5001 via Waitress)
- Builder Service pattern: builder_service/ (port 8100)
- Start script: shortcuts\00_Start-Restart_AIHub_Services_V3.bat
- Conda env: aihubbuilder
- DB: SQL Server with RLS via sp_setTenantContext

## Tasks

### 1. Start Script
Edit `shortcuts\00_Start-Restart_AIHub_Services_V3.bat`:
- Add Command Center as service 13 after Builder Data
- Same pattern as builder (line ~107), use aihubbuilder conda env, python main.py
- Add to summary echo section

### 2. Main App Route  
Add to `app.py` (follow /builder pattern at lines ~1519-1614):
- `/command-center` route: generate token, redirect to http://localhost:5091?token=TOKEN
- `/api/validate-cc-token` POST endpoint
- `/api/cc-auto-token` GET endpoint
- Use CC_SERVICE_PORT env var (default 5091)

### 3. Nav Menu
Add Command Center link in `templates/base.html` sidebar nav with fa-satellite-dish icon.

### 4. Landscape Scanner Auth
Verify `command_center/orchestration/landscape_scanner.py` auth headers are correct.

### 5. Delegator
Check if `/api/agents/{id}/chat` exists in app.py. If not, find correct endpoint and update `command_center/orchestration/delegator.py`.

### 6. User Memory SQL
Wire `command_center/memory/user_memory.py` to cc_UserMemory table using pyodbc. Follow the pattern from routes/data_explorer.py `_dashboard_db_execute`.

### 7. Dark Theme CSS
Update `command_center_service/static/css/command-center.css`:
- Backgrounds: #000, #0a0a0a, #111, #18181b
- Text: #fff, #a1a1aa, #71717a  
- Accents: #06b6d4 (cyan), #a78bfa (violet)
- Fonts: Outfit + JetBrains Mono

### 8. Frontend Fonts
Update `command_center_service/static/index.html` to load Outfit and JetBrains Mono from Google Fonts.

## Rules
- DO NOT start/restart services
- DO NOT modify builder service code
- Only ADD new code
