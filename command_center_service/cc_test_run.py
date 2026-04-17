import sys, asyncio, json
sys.path.insert(0, '..')
import os
os.environ['LANGCHAIN_TRACING_V2'] = 'false'
os.environ['LANGSMITH_TRACING'] = 'false'

from langchain_core.messages import HumanMessage, AIMessage
from graph.cc_graph import create_command_center_graph
from command_center.orchestration.landscape_scanner import invalidate_cache
invalidate_cache()

async def test():
    graph = create_command_center_graph()

    print('=== TEST 1: Data query (auto-pick agent) ===')
    state1 = {
        'messages': [HumanMessage(content='show me sales by region for last year')],
        'session_id': 'test-001',
    }
    r1 = await graph.ainvoke(state1, config={'configurable': {'thread_id': 'test-001'}})
    for m in reversed(r1.get('messages', [])):
        if hasattr(m, 'type') and m.type == 'ai':
            try:
                blocks = json.loads(m.content)
                for b in blocks:
                    print(b.get('content', '')[:300])
            except:
                print(m.content[:300])
            break
    ad1 = r1.get('active_delegation')
    ps1 = r1.get('pending_agent_selection')
    agent_name = ad1.get('agent_name') if ad1 else 'None'
    print(f'Active agent: {agent_name}')
    print(f'Pending selection: {ps1}')
    print()

asyncio.run(test())
