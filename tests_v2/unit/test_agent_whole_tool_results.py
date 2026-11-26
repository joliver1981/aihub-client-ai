"""Every tool of The Agent returns its text whole, as far as one tool result carries.

The runtime under the SDK replaces a text result longer than 50,000 characters with a 2 KB
preview unless the tool declares a larger size, up to the runtime's maximum of 500,000.
Only two tools declared it. Several others cut their own text far below even the default:
an integration's result and a personal connection's result at 2,500 characters, what Python
code printed at 18,000 (with no sign of it), an automation's code at 4,000, a portal page
reading at 1,500, an e-mail body at 15,000, a table list at 200 tables and a column list at
120 columns (the table list with no sign of it).

What must hold (agent_service/result_size.py and the tools):
- every tool of the server declares the runtime's maximum;
- those tools return their text whole;
- a result longer than the maximum is handed over in parts with nothing left out (the
  first by the tool, the rest by read_more): never the runtime's silent 2 KB preview.

Needs the agent env (claude_agent_sdk); self-skips elsewhere. Standalone, with that env's
python: tests_v2/unit/test_agent_whole_tool_results.py
"""
import asyncio
import json
import logging
import os
import sys
import unittest

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import result_size as rs                          # noqa: E402
    import brain                                      # noqa: E402
    import authoring_tools                            # noqa: E402
    import code_tools                                 # noqa: E402
    import document_tools                             # noqa: E402
    import integration_tools                          # noqa: E402
    import platform_tools                             # noqa: E402
    import portal_tools                               # noqa: E402
    from claude_agent_sdk import tool                 # noqa: E402
    from mcp.types import ToolAnnotations             # noqa: E402
    from platform_tools import CURRENT_USER           # noqa: E402
    HAVE_SDK = True
except Exception as e:                                # pragma: no cover
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(reason=f"needs the aihub-agent env: {_IMPORT_ERR}")
    except ImportError:
        pass


def _run(coro):
    return asyncio.run(coro)


def _txt(result):
    return "".join(b.get("text", "") for b in (result or {}).get("content", []) if isinstance(b, dict))


def _text_result(text, **extra):
    return {"content": [{"type": "text", "text": text}], **extra}


def _numbered(chars, first="FIRST-LINE", last="LAST-LINE"):
    lines, size, i = [first], len(first) + 1, 0
    while size < chars:
        i += 1
        ln = f"row {i:07d} of the result"
        lines.append(ln)
        size += len(ln) + 1
    lines.append(last)
    return "\n".join(lines)


@unittest.skipUnless(HAVE_SDK, "agent_service not importable in this env")
class _Case(unittest.TestCase):
    def setUp(self):
        self._saved = (rs.RESULT_MAX_CHARS, rs.WHOLE_RESULT, rs.logger)
        rs.logger = logging.getLogger("test_agent_whole_tool_results.silent")   # the cut is logged as a warning
        rs.logger.addHandler(logging.NullHandler())
        rs.logger.propagate = False
        self._token = CURRENT_USER.set({"user_id": 987655, "username": "whole-test", "role": 3,
                                        "name": "Whole Test", "session_id": "conversation-w"})

    def tearDown(self):
        rs.RESULT_MAX_CHARS, rs.WHOLE_RESULT, rs.logger = self._saved
        CURRENT_USER.reset(self._token)


class TestEveryToolDeclaresTheMaximum(_Case):

    def test_every_tool_of_the_server_declares_500000(self):
        tools = brain.AIHUB_SERVER_TOOLS
        self.assertGreater(len(tools), 100)
        missing = [t.name for t in tools if getattr(t.annotations, "maxResultSizeChars", None) != 500_000]
        self.assertEqual(missing, [])
        self.assertEqual(len({t.name for t in tools}), len(tools))

    def test_the_tools_themselves_are_not_changed_only_the_servers_copies(self):
        # other code and the tests call the module-level tools directly
        self.assertIsNone(code_tools.run_python.annotations)
        server_copy = next(t for t in brain.AIHUB_SERVER_TOOLS if t.name == "run_python")
        self.assertIsNot(server_copy, code_tools.run_python)
        self.assertEqual(server_copy.description, code_tools.run_python.description)
        self.assertEqual(server_copy.input_schema, code_tools.run_python.input_schema)

    def test_a_tool_that_manages_its_own_size_is_passed_through_untouched(self):
        for own in (document_tools.search_documents, document_tools.read_file):
            self.assertIs(next(t for t in brain.AIHUB_SERVER_TOOLS if t.name == own.name), own)

    def test_a_tools_other_hints_are_kept(self):
        @tool("zz_hinted", "a tool with a hint of its own", {"type": "object", "properties": {}},
              annotations=ToolAnnotations(readOnlyHint=True))
        async def zz_hinted(args):
            return _text_result("ok")
        out = rs.declare_whole_results([zz_hinted])[0]
        self.assertTrue(out.annotations.readOnlyHint)
        self.assertEqual(out.annotations.maxResultSizeChars, 500_000)
        self.assertIsNone(getattr(zz_hinted.annotations, "maxResultSizeChars", None))

    def test_an_sdk_that_cannot_declare_a_size_leaves_the_tools_as_they_are(self):
        rs.WHOLE_RESULT = None
        tools = [code_tools.run_python, document_tools.search_documents]
        self.assertEqual(rs.declare_whole_results(tools), tools)

    def test_the_sdk_really_carries_the_declaration_to_the_runtime(self):
        from claude_agent_sdk import create_sdk_mcp_server
        import inspect
        self.assertIn('"anthropic/maxResultSizeChars"', inspect.getsource(create_sdk_mcp_server))
        self.assertEqual((rs.RESULT_MAX_CHARS, rs.result_budget()), (500_000, 495_000))


class TestAResultLongerThanOneResultCarries(_Case):
    """Nothing is left out: the result is handed over in parts, the first by the tool, the rest by read_more."""

    def setUp(self):
        super().setUp()
        self._kept = (dict(rs._kept_results), rs._KEPT_RESULT_SECONDS, rs._KEPT_RESULT_TOTAL_CHARS)
        rs._kept_results.clear()

    def tearDown(self):
        rs._kept_results.clear()
        rs._kept_results.update(self._kept[0])
        rs._KEPT_RESULT_SECONDS, rs._KEPT_RESULT_TOTAL_CHARS = self._kept[1], self._kept[2]
        super().tearDown()

    @staticmethod
    def _split(text):
        """(the part's own text, its note) of one result."""
        body, note = text.rsplit("\n\n[PART ", 1)
        return body, "[PART " + note

    @staticmethod
    def _rid(note):
        return note.split('result_id="', 1)[1].split('"', 1)[0]

    def _read_all(self, first_text):
        body, note = self._split(first_text)
        bodies, notes = [body], [note]
        while "read_more" in notes[-1]:
            part = int(notes[-1].split("part=", 1)[1].split(")", 1)[0])
            text = _txt(_run(rs.read_more.handler({"result_id": self._rid(notes[-1]), "part": part})))
            body, note = self._split(text)
            bodies.append(body)
            notes.append(note)
        return bodies, notes

    def test_a_result_that_fits_is_returned_as_it_is(self):
        for chars in (10, 60_000, 494_000):
            r = _text_result(_numbered(chars))
            self.assertIs(rs.fit_result(r, "t"), r, chars)
        self.assertEqual(rs._kept_results, {})

    def test_a_longer_result_comes_in_parts_and_the_parts_are_the_whole_text(self):
        whole = _numbered(1_300_000)
        first = _txt(rs.fit_result(_text_result(whole), "probe_connection_query"))
        self.assertLessEqual(len(first), 500_000)               # over it the runtime shows a 2 KB preview
        self.assertTrue(first.startswith("FIRST-LINE\nrow 0000001 of the result"))
        self.assertIn("[PART 1 OF 3: this result of probe_connection_query is "
                      f"{len(whole):,} characters long and one tool result carries 500,000.", first)
        self.assertIn("Nothing is left out", first)
        bodies, notes = self._read_all(first)
        self.assertEqual(len(bodies), 3)
        self.assertEqual("".join(bodies), whole)                # every character, once, in order
        self.assertTrue(all(len(b) + 600 < 500_000 for b in bodies))
        self.assertTrue(all(b.endswith("\n") for b in bodies[:-1]))          # cut at the end of a line
        self.assertIn("[PART 3 OF 3, the last one", notes[-1])
        self.assertIn("You now have all of it", notes[-1])
        self.assertTrue(bodies[-1].endswith("LAST-LINE"))

    def test_text_with_no_line_ends_is_still_whole_across_the_parts(self):
        whole = "x" * 700_000 + "END"
        bodies, _notes = self._read_all(_txt(rs.fit_result(_text_result(whole), "t")))
        self.assertEqual("".join(bodies), whole)

    def test_the_error_flag_and_a_picture_are_kept(self):
        r = {"content": [{"type": "text", "text": _numbered(700_000)},
                         {"type": "image", "data": "aGk=", "mimeType": "image/png"}], "is_error": True}
        out = rs.fit_result(r, "t")
        self.assertTrue(out["is_error"])
        self.assertEqual([b["type"] for b in out["content"]], ["text", "image"])
        self.assertLessEqual(len(_txt(out)), 500_000)
        self.assertGreater(len(r["content"][0]["text"]), 600_000)             # the input is not changed

    def test_several_text_parts_of_one_result_are_counted_together(self):
        r = {"content": [{"type": "text", "text": "A" * 300_000}, {"type": "text", "text": "B" * 300_000}]}
        bodies, _notes = self._read_all(_txt(rs.fit_result(r, "t")))
        self.assertEqual("".join(bodies), "A" * 300_000 + "\n" + "B" * 300_000)

    def test_what_is_not_a_text_result_is_left_alone(self):
        for r in (None, "text", {"content": "text"}, {"no": "content"}, {"content": []}):
            self.assertEqual(rs.fit_result(r, "t"), r)

    def test_only_the_person_and_the_conversation_that_got_part_one_can_read_on(self):
        first = _txt(rs.fit_result(_text_result(_numbered(700_000)), "t"))
        rid = self._rid(self._split(first)[1])
        mine = _run(rs.read_more.handler({"result_id": rid, "part": 2}))
        self.assertFalse(mine["is_error"])
        for other in ({"user_id": 555002, "username": "someone-else", "role": 3, "session_id": "conversation-w"},
                      {"user_id": 987655, "username": "whole-test", "role": 3, "session_id": "another-conversation"},
                      {"user_id": 0, "username": "service", "role": 3}):
            token = CURRENT_USER.set(other)
            try:
                res = _run(rs.read_more.handler({"result_id": rid, "part": 2}))
            finally:
                CURRENT_USER.reset(token)
            self.assertTrue(res["is_error"], other)
            self.assertIn("There is no kept result with the id", _txt(res))   # as if it did not exist
            self.assertNotIn("row 00", _txt(res))

    def test_a_wrong_id_or_part_says_so(self):
        first = _txt(rs.fit_result(_text_result(_numbered(700_000)), "t"))
        rid = self._rid(self._split(first)[1])
        self.assertIn("There is no kept result", _txt(_run(rs.read_more.handler({"result_id": "nope", "part": 2}))))
        for bad in (0, 3, -1, "x"):
            res = _run(rs.read_more.handler({"result_id": rid, "part": bad}))
            self.assertTrue(res["is_error"])
            self.assertIn("That result has 2 parts", _txt(res))

    def test_the_parts_are_kept_for_a_while_and_reading_one_keeps_them_longer(self):
        first = _txt(rs.fit_result(_text_result(_numbered(1_300_000)), "t"))
        rid = self._rid(self._split(first)[1])
        rs._kept_results[rid]["when"] -= rs._KEPT_RESULT_SECONDS - 5          # almost gone
        self.assertFalse(_run(rs.read_more.handler({"result_id": rid, "part": 2}))["is_error"])
        rs._kept_results[rid]["when"] -= rs._KEPT_RESULT_SECONDS - 5          # reading part 2 kept it alive
        self.assertFalse(_run(rs.read_more.handler({"result_id": rid, "part": 3}))["is_error"])
        rs._kept_results[rid]["when"] -= rs._KEPT_RESULT_SECONDS + 5          # now really past its time
        gone = _run(rs.read_more.handler({"result_id": rid, "part": 3}))
        self.assertTrue(gone["is_error"])
        self.assertIn("call the tool that produced it again", _txt(gone))

    def test_kept_results_stay_inside_their_memory_and_the_oldest_goes_first(self):
        rs._KEPT_RESULT_TOTAL_CHARS = 1_500_000
        first_rid = self._rid(self._split(_txt(rs.fit_result(_text_result(_numbered(800_000)), "a")))[1])
        second_rid = self._rid(self._split(_txt(rs.fit_result(_text_result(_numbered(800_000)), "b")))[1])
        self.assertEqual(list(rs._kept_results), [second_rid])
        self.assertTrue(_run(rs.read_more.handler({"result_id": first_rid, "part": 2}))["is_error"])
        self.assertFalse(_run(rs.read_more.handler({"result_id": second_rid, "part": 2}))["is_error"])

    def test_one_result_larger_than_the_memory_is_still_kept_for_its_own_parts(self):
        rs._KEPT_RESULT_TOTAL_CHARS = 100_000
        whole = _numbered(1_300_000)
        bodies, _notes = self._read_all(_txt(rs.fit_result(_text_result(whole), "t")))
        self.assertEqual("".join(bodies), whole)

    def test_the_servers_copy_of_a_tool_is_guarded(self):
        @tool("zz_big", "returns a text of the size asked for", {"type": "object", "properties": {}})
        async def zz_big(args):
            return _text_result(_numbered(int(args["chars"])), is_error=bool(args.get("fail")))
        guarded = rs.declare_whole_results([zz_big])[0]
        small = _run(guarded.handler({"chars": 70_000}))
        self.assertTrue(_txt(small).endswith("LAST-LINE"))                       # over the old default, whole
        self.assertNotIn("[PART ", _txt(small))
        big = _run(guarded.handler({"chars": 900_000, "fail": True}))
        self.assertLessEqual(len(_txt(big)), 500_000)
        self.assertIn("[PART 1 OF 2", _txt(big))
        self.assertTrue(big["is_error"])
        # the tool itself, called directly, still returns everything
        self.assertGreater(len(_txt(_run(zz_big.handler({"chars": 900_000})))), 900_000)

    def test_read_more_is_a_tool_of_the_server_and_of_the_read_only_side_threads(self):
        self.assertIn("read_more", [t.name for t in brain.AIHUB_SERVER_TOOLS])
        self.assertIn("read_more", brain._READ_TOOL_NAMES)
        self.assertIs(next(t for t in brain.AIHUB_SERVER_TOOLS if t.name == "read_more"), rs.read_more)


class TestWithNobodyToHandPartsTo(_Case):
    """A call outside a person's turn: the text is cut, with its beginning and end kept, and says so."""

    def setUp(self):
        super().setUp()
        self._nobody = CURRENT_USER.set({"user_id": 0, "username": "service", "role": 3})

    def tearDown(self):
        CURRENT_USER.reset(self._nobody)
        super().tearDown()

    def test_the_beginning_and_the_end_are_kept_and_the_gap_is_stated(self):
        whole = _numbered(1_300_000)
        out = _txt(rs.fit_result(_text_result(whole), "probe_connection_query"))
        self.assertLessEqual(len(out), 500_000)
        self.assertGreater(len(out), 480_000)
        self.assertTrue(out.startswith("FIRST-LINE\nrow 0000001 of the result"))
        self.assertIn("LAST-LINE", out)                         # the end: totals, notes, error lines
        left = int(out.split("[... ", 1)[1].split(" characters left out here ...]", 1)[0].replace(",", ""))
        self.assertGreater(left, 800_000)
        self.assertTrue(out.rstrip().endswith("work on the file.]"))
        self.assertIn(f"this result is {len(whole):,} characters long and one tool result carries 500,000", out)
        self.assertIn(f"{left:,} characters in between were left out", out)
        head = out.split("\n\n[... ", 1)[0]
        tail = out.split(" characters left out here ...]\n\n", 1)[1].split("\n\n[NOT ALL SHOWN", 1)[0]
        self.assertEqual(len(head) + len(tail) + left, len(whole))
        self.assertTrue(whole.startswith(head) and whole.endswith(tail))
        self.assertEqual(rs._kept_results, {})

    def test_with_an_sdk_that_cannot_declare_the_size_the_cut_is_at_the_runtime_default(self):
        rs.RESULT_MAX_CHARS = 50_000
        out = _txt(rs.fit_result(_text_result(_numbered(120_000)), "t"))
        self.assertLessEqual(len(out), 50_000)
        self.assertIn("one tool result carries 50,000", out)


class TestToolsThatCutTheirOwnText(_Case):
    """Each of these returned less than the runtime allowed even before: a size of its own."""

    def _patched(self, module, **names):
        saved = {k: getattr(module, k) for k in names}
        for k, v in names.items():
            setattr(module, k, v)
        self.addCleanup(lambda: [setattr(module, k, v) for k, v in saved.items()])

    def test_an_integrations_result_is_whole(self):
        files = [{"name": f"Invoice {i:04d}.pdf", "id": f"file-{i:04d}", "modified": "2026-11-01"} for i in range(400)]

        async def accessible(integration_id):
            return {"integration_id": integration_id, "integration_name": "Files"}, None

        async def post(path, body, timeout=None):
            return {"status": "success", "result": {"files": files}}, 200
        self._patched(integration_tools, _resolve_accessible=accessible, _post=post)
        out = _txt(_run(integration_tools.execute_integration_operation.handler(
            {"integration_id": 7, "operation": "search_files", "parameters_json": "{}"})))
        self.assertGreater(len(out), 25_000)                    # it was a 2,500-character preview
        self.assertIn("file-0399", out)
        self.assertNotIn("truncated", out)
        payload = json.loads(out.split("succeeded:\n", 1)[1])
        self.assertEqual(len(payload["files"]), 400)            # the model gets valid, complete JSON

    def test_everything_python_code_printed_is_returned(self):
        printed = _numbered(60_000)

        async def execute(uid, code, extra_files=None, lane=None):
            return {"configured": True, "ok": True, "timed_out": False, "returncode": 0, "output": printed,
                    "links": [], "produced": [], "manifest": "", "error": None}
        self._patched(code_tools, execute_python=execute)
        out = _txt(_run(code_tools.run_python.handler({"code": "print('x')"})))
        self.assertTrue(out.startswith("FIRST-LINE"))
        self.assertIn("LAST-LINE", out)                         # it was cut at 18,000 with no sign of it
        self.assertGreaterEqual(len(out), 60_000)

    def test_the_real_python_lane_returns_more_than_50000_characters(self):
        # through the real interpreter: the shared lane cuts at 50,000 for its other callers
        from code_exec import resolve_interpreter
        if not resolve_interpreter():
            self.skipTest("no code interpreter configured on this machine")
        code = "\n".join(["for i in range(3000):",
                          "    print(f'row {i:05d} of a long listing that goes on a bit')",
                          "print('THE-END-OF-THE-OUTPUT')", ""])
        r = _run(code_tools.execute_python(987655, code, stage_uploads=False))
        self.assertTrue(r["ok"], r.get("error"))
        self.assertGreater(len(r["output"]), 120_000)
        self.assertIn("row 02999 of a long listing", r["output"])
        self.assertTrue(r["output"].rstrip().endswith("THE-END-OF-THE-OUTPUT"))
        self.assertNotIn("truncated at", r["output"])

    def test_an_automations_code_is_shown_whole(self):
        code = "import aihub_runtime as aihub\n" + "\n".join(f"step_{i} = {i}" for i in range(4000)) + "\n# END-OF-CODE"
        manifest = {"inputs": [{"name": f"input_{i}", "type": "string", "description": "d" * 40} for i in range(40)]}

        async def resolve(automation_id):
            return "auto-1", None

        async def manage(action, payload, timeout=None):
            return {"automation": {"name": "Long one", "automation_id": "auto-1", "status": "active",
                                   "current_version": 3, "pinned_version": 3, "versions": [1, 2, 3],
                                   "description": "d", "manifest": manifest, "code": code}}, 200
        self._patched(authoring_tools, _resolve_automation=resolve, _manage=manage)
        out = _txt(_run(authoring_tools.get_automation.handler({"automation_id": "Long one"})))
        self.assertGreater(len(code), 40_000)
        self.assertIn(code, out)                                # it was the first 4,000 characters
        self.assertTrue(out.rstrip().endswith("# END-OF-CODE"))
        self.assertNotIn("(truncated)", out)
        self.assertIn(json.dumps(manifest), out)                # and the manifest its first 600

    def test_an_agents_objective_is_shown_whole(self):
        import agent_builder_tools as abt
        objective = "You are the compliance assistant. " + "Follow this rule exactly. " * 120 + "FINAL-INSTRUCTION."
        shown = abt._fmt_agent({"id": 7, "name": "Compliance", "objective": objective, "enabled": True,
                                "core_tools": [], "custom_tools": []})
        self.assertGreater(len(objective), 3_000)
        self.assertIn("Objective: " + objective, shown)         # it was the first 600 characters and an ellipsis
        self.assertIn("FINAL-INSTRUCTION.", shown)

    def test_all_the_browser_agent_read_on_a_portal_page_is_relayed(self):
        reading = "Account summary. " + "Line of the statement table. " * 300 + "CLOSING-BALANCE 5,751.70"
        self._patched(portal_tools, _stage_files=lambda uid, files: ([], [], []))
        out = _txt(portal_tools._finish_run({"status": "ok", "files": [], "final_result": reading}, 1))
        self.assertIn("CLOSING-BALANCE 5,751.70", out)          # it was the first 1,500 characters
        self.assertIn(reading, out)

    def test_every_table_of_a_database_is_listed(self):
        tables = [{"TABLE_NAME": f"tbl_{i:04d}", "is_documented": i % 7 == 0} for i in range(1, 1201)]

        async def row(connection):
            return {"id": 20, "name": "ERP"}, None

        async def get(path, *a, **k):
            return {"tables": tables}
        self._patched(platform_tools, _resolve_connection_row=row, _get=get,
                      _resolution_note=lambda asked, r: "")
        out = _txt(_run(platform_tools.get_connection_schema.handler({"connection": "ERP"})))
        self.assertEqual(out.count("\n- tbl_"), 1200)           # the list stopped at 200 without saying so
        self.assertIn("- tbl_1200", out)

    def test_every_column_of_a_table_is_listed(self):
        cols = [{"COLUMN_NAME": f"col_{i:03d}", "DATA_TYPE": "varchar"} for i in range(1, 301)]

        async def row(connection):
            return {"id": 20, "name": "ERP"}, None

        async def get(path, *a, **k):
            return {"success": True, "table": "wide_table", "source": "live", "columns": cols}
        self._patched(platform_tools, _resolve_connection_row=row, _get=get,
                      _resolution_note=lambda asked, r: "")
        out = _txt(_run(platform_tools.get_connection_schema.handler({"connection": "ERP", "table": "wide_table"})))
        self.assertEqual(out.count("\n- col_"), 300)            # it was the first 120
        self.assertNotIn("more columns", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
