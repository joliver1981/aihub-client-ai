# introspect_browser_use.py - print the installed browser-use API surface.
# Run with the isolated env's python DIRECTLY (not `conda run`, which rejects newlines):
#   C:\Users\james\miniconda3\envs\aihub-browseruse\python.exe browser_use_service\introspect_browser_use.py
# Used by task #9 to pin portal_runner.py against the real browser-use 0.12.x API.
import inspect

import browser_use as b

print("VERSION", getattr(b, "__version__", "?"))
print("TOP", sorted(x for x in dir(b) if not x.startswith("_")))
for name in ["ChatOpenAI", "ChatAnthropic", "ChatGoogle", "Agent", "Browser",
             "BrowserSession", "BrowserProfile", "BrowserConfig", "Chrome", "Tools", "Controller"]:
    print("HAS", name, hasattr(b, name))

try:
    from browser_use import Agent
    print("AGENT_PARAMS", list(inspect.signature(Agent.__init__).parameters))
except Exception as e:
    print("AGENT_ERR", repr(e))

for mod in ["BrowserProfile", "BrowserSession", "Browser", "BrowserConfig"]:
    cls = getattr(b, mod, None)
    if cls is not None:
        try:
            params = list(inspect.signature(cls.__init__).parameters)
            print(mod, "PARAMS", params)
        except Exception as e:
            print(mod, "ERR", repr(e))

try:
    import browser_use.llm as L
    print("LLM_MOD", sorted(x for x in dir(L) if not x.startswith("_")))
except Exception as e:
    print("LLM_MOD_ERR", repr(e))
