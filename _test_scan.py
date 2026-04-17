import sys, asyncio
sys.path.insert(0, 'command_center_service')
sys.path.insert(0, '.')
import cc_config
from command_center.orchestration.landscape_scanner import scan_platform, format_landscape_summary
r = asyncio.run(scan_platform())
s = format_landscape_summary(r)
print(f'Summary length: {len(s)} chars')
print(f'All agents: {len(r.get("all_agents", []))}')
print(f'Data agents: {len(r.get("data_agents", []))}')
print(f'General agents: {len(r.get("agents", []))}')
for a in r.get('data_agents', [])[:3]:
    print(f'  [{a["agent_id"]}] {a["agent_name"]}: {a.get("description", "")[:80]}')
print()
print('--- SUMMARY (first 1000 chars) ---')
print(s[:1000])
