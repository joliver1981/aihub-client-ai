"""
Per-user connection keying in the MCP gateway (Blocker B, 2026-09-03).

The gateway used to hold ONE connection per server_id with the per-user
bearer baked into the transport, so two users' agents sharing a server_id
shared a token. These tests pin the composite key: the same server_id
connected for two users yields two independent connections; a caller that
passes no user keeps the legacy server_id-only connection; disconnecting one
never touches the other; and tool calls route to the caller's own transport.

They use the sample stdio server so nothing needs a network. Run standalone
(any python with the gateway deps: `python test_user_scoped_connections.py`)
or under pytest — the async bodies are driven with asyncio.run so
pytest-asyncio is not required.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from server_manager import MCPServerManager, connection_key, split_key  # noqa: E402

SAMPLE = os.path.join(os.path.dirname(__file__), "sample_mcp_server.py")


def _config(tag: str) -> dict:
    # The env var is the per-connection "identity" the sample server can
    # reflect back, standing in for a per-user bearer header.
    return {"type": "local", "command": sys.executable, "args": [SAMPLE],
            "env_vars": {"CONN_TAG": tag}}


def test_connection_key_shapes():
    assert connection_key("30") == "30"
    assert connection_key("30", None) == "30"
    assert connection_key("30", "") == "30"
    assert connection_key("30", 0) == "30"          # service principal = no user
    assert connection_key("30", "0") == "30"
    assert connection_key("30", 13) == "30@u13"
    assert connection_key(30, "13") == "30@u13"
    assert split_key("30@u13") == ("30", "13")
    assert split_key("30") == ("30", None)


async def _two_users_two_connections():
    m = MCPServerManager()
    try:
        a = await m.connect("30", _config("user-a"), user_id="13")
        b = await m.connect("30", _config("user-b"), user_id="77")
        legacy = await m.connect("30", _config("shared"))
        assert a["status"] == b["status"] == legacy["status"] == "connected"
        assert a["connection_key"] == "30@u13"
        assert b["connection_key"] == "30@u77"
        assert legacy["connection_key"] == "30"

        conns = m.get_all_connections()
        assert set(conns) == {"30@u13", "30@u77", "30"}, conns
        assert conns["30@u13"]["user_id"] == "13" and conns["30@u13"]["server_id"] == "30"
        assert conns["30"]["user_id"] is None

        # Three distinct transports — no sharing.
        t13 = m._connections["30@u13"]["transport"]
        t77 = m._connections["30@u77"]["transport"]
        tl = m._connections["30"]["transport"]
        assert t13 is not t77 and t77 is not tl and t13 is not tl
        assert m._connections["30@u13"]["config"]["env_vars"]["CONN_TAG"] == "user-a"
        assert m._connections["30@u77"]["config"]["env_vars"]["CONN_TAG"] == "user-b"

        # Status is per key.
        assert (await m.get_status("30", user_id="13"))["connection_key"] == "30@u13"
        assert (await m.get_status("30", user_id="99"))["status"] == "disconnected"

        # Tool calls route to the caller's own connection; the unknown user
        # gets "not connected", never someone else's transport.
        r = await m.call_tool("30", "echo", {"message": "hi from 13"}, user_id="13")
        assert r["status"] == "success" and "hi from 13" in r["result"]
        r = await m.call_tool("30", "echo", {"message": "x"}, user_id="99")
        assert r["status"] == "error" and "30@u99" in r["error"]

        # Tools are listed per key too.
        assert len(await m.list_tools("30", user_id="77")) == 3
        try:
            await m.list_tools("30", user_id="99")
            assert False, "expected ConnectionError for an unconnected user"
        except ConnectionError:
            pass

        # Disconnecting one user leaves the other user AND the legacy
        # connection untouched (the old code tore down the shared one).
        assert (await m.disconnect("30", user_id="13"))["status"] == "disconnected"
        assert set(m.get_all_connections()) == {"30@u77", "30"}
        r = await m.call_tool("30", "echo", {"message": "still 77"}, user_id="77")
        assert r["status"] == "success" and "still 77" in r["result"]
        r = await m.call_tool("30", "echo", {"message": "legacy"})
        assert r["status"] == "success" and "legacy" in r["result"]

        # Reconnecting the same user replaces only that user's connection.
        again = await m.connect("30", _config("user-b-2"), user_id="77")
        assert again["connection_key"] == "30@u77"
        assert m._connections["30@u77"]["config"]["env_vars"]["CONN_TAG"] == "user-b-2"
        assert m._connections["30"]["config"]["env_vars"]["CONN_TAG"] == "shared"
    finally:
        await m.cleanup_all()
    assert m.get_all_connections() == {}


def test_two_users_two_connections():
    asyncio.run(_two_users_two_connections())


async def _concurrent_connects_do_not_cross():
    """Two users connecting the SAME server concurrently (the GeneralAgent
    race) end with their own connections, each answering as itself."""
    m = MCPServerManager()
    try:
        results = await asyncio.gather(
            m.connect("30", _config("A"), user_id="1"),
            m.connect("30", _config("B"), user_id="2"),
            m.connect("30", _config("C"), user_id="3"),
        )
        assert [r["connection_key"] for r in results] == ["30@u1", "30@u2", "30@u3"]
        tags = {k: v["config"]["env_vars"]["CONN_TAG"] for k, v in m._connections.items()}
        assert tags == {"30@u1": "A", "30@u2": "B", "30@u3": "C"}
        calls = await asyncio.gather(
            m.call_tool("30", "echo", {"message": "one"}, user_id="1"),
            m.call_tool("30", "echo", {"message": "two"}, user_id="2"),
            m.call_tool("30", "echo", {"message": "three"}, user_id="3"),
        )
        assert [("one" in c["result"], "two" in c["result"], "three" in c["result"])
                for c in calls] == [(True, False, False), (False, True, False), (False, False, True)]
    finally:
        await m.cleanup_all()


def test_concurrent_connects_do_not_cross():
    asyncio.run(_concurrent_connects_do_not_cross())


async def _annotations_pass_through():
    """tools/list annotations (when a server sends them) survive _fetch_tools;
    the sample server sends none, so the field must simply be absent, never
    fabricated."""
    m = MCPServerManager()
    try:
        r = await m.connect("s", _config("x"))
        assert r["status"] == "connected"
        for t in r["tools"]:
            assert set(t) <= {"name", "description", "inputSchema", "annotations"}
            assert "annotations" not in t
    finally:
        await m.cleanup_all()


def test_annotations_pass_through():
    asyncio.run(_annotations_pass_through())


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {e!r}")
    sys.exit(1 if failed else 0)
