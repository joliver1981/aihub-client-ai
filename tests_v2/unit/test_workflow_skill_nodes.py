"""Workflow skills on the AI nodes (2026-09-22, James).

Covers workflow_skills.py (list / read / resolve / describe against a temp
skills root) and the AIExtractExecutor prompt: a config WITHOUT the new key
builds the byte-identical prompt it always did, and a config WITH skill text
gets one SKILL GUIDANCE block in the right place.

Standalone (python test_workflow_skill_nodes.py) or pytest. No app, no DB,
no network: the skills root is redirected with WORKFLOW_SKILLS_ROOT.
"""
import os
import shutil
import sys
import tempfile
import unittest

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)

_TMP = tempfile.mkdtemp(prefix="wf_skills_")
os.environ["WORKFLOW_SKILLS_ROOT"] = _TMP      # must be set BEFORE the import

import workflow_skills as ws                    # noqa: E402
from ai_extract_executor import AIExtractExecutor  # noqa: E402


def _write(scope, name, text):
    d = os.path.join(_TMP, scope, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(text)


PRODUCT_MULTILINE = (
    "---\n"
    "name: aihub-sample\n"
    "description: Use when building sample things in AI Hub\n"
    "  across several lines of description.\n"
    "---\n"
    "\n"
    "# Sample\n"
    "\n"
    "Body of the product skill.\n"
)
TENANT_ROUTING = (
    "---\n"
    "name: horizon-team-routing\n"
    "description: Use when filing requirements to Horizon teams\n"
    "---\n"
    "\n"
    "Finance owns chargebacks. Logistics owns bookings.\n"
)


class SkillFilesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shutil.rmtree(_TMP, ignore_errors=True)
        os.makedirs(_TMP)
        _write("product", "aihub-sample", PRODUCT_MULTILINE)
        _write("tenant", "horizon-team-routing", TENANT_ROUTING)
        _write("product", "shared-name", "---\nname: shared-name\ndescription: product copy\n---\n\nPRODUCT BODY\n")
        _write("tenant", "shared-name", "---\nname: shared-name\ndescription: tenant copy\n---\n\nTENANT BODY\n")
        os.makedirs(os.path.join(_TMP, "tenant", "no-skill-file"))          # ignored
        os.makedirs(os.path.join(_TMP, "product", "Bad Name"))              # ignored
        _write("user-ignored", "private-skill", "---\nname: private-skill\ndescription: x\n---\n\nPRIVATE\n")

    def test_list_is_sorted_deduped_and_parsed(self):
        skills = ws.list_skills()
        names = [s["name"] for s in skills]
        self.assertEqual(names, sorted(names))
        self.assertEqual(names, ["aihub-sample", "horizon-team-routing", "shared-name"])
        by_name = {s["name"]: s for s in skills}
        self.assertEqual(by_name["shared-name"]["scope"], "tenant")          # tenant wins
        self.assertEqual(by_name["aihub-sample"]["description"],
                         "Use when building sample things in AI Hub across several lines of description.")
        self.assertEqual(by_name["aihub-sample"]["size"], len("# Sample\n\nBody of the product skill."))
        self.assertNotIn("private-skill", names)                            # user scope never offered

    def test_read_strips_frontmatter_and_prefers_tenant(self):
        s = ws.read_skill("shared-name")
        self.assertEqual((s["scope"], s["content"], s["description"]), ("tenant", "TENANT BODY", "tenant copy"))
        p = ws.read_skill("aihub-sample")
        self.assertEqual(p["scope"], "product")
        self.assertTrue(p["content"].startswith("# Sample"))
        self.assertNotIn("---", p["content"])
        self.assertIsNone(ws.read_skill("missing-skill"))
        self.assertIsNone(ws.read_skill("../escape"))

    def test_resolve_without_keys_is_empty(self):
        self.assertEqual(ws.resolve_node_skill({}), "")
        self.assertEqual(ws.resolve_node_skill(None), "")
        self.assertEqual(ws.resolve_node_skill({"skillName": "", "skillText": "   "}), "")
        self.assertEqual(ws.describe_node_skill({}), "none")

    def test_resolve_named_then_inline(self):
        text = ws.resolve_node_skill({"skillName": "horizon-team-routing",
                                      "skillText": " Tie-break: ties go to Finance. "})
        self.assertEqual(text, "Finance owns chargebacks. Logistics owns bookings.\n\n"
                               "Tie-break: ties go to Finance.")
        self.assertEqual(ws.describe_node_skill({"skillName": "horizon-team-routing",
                                                 "skillText": "abc"}),
                         "skill 'horizon-team-routing' + embedded text")
        self.assertEqual(ws.describe_node_skill({"skillText": "abc"}), "embedded text")

    def test_resolve_failures_are_loud(self):
        with self.assertRaises(ValueError) as cm:
            ws.resolve_node_skill({"skillName": "does-not-exist"})
        self.assertIn("does-not-exist", str(cm.exception))
        with self.assertRaises(ValueError):
            ws.resolve_node_skill({"skillName": "Not Valid!"})
        with self.assertRaises(ValueError):
            ws.resolve_node_skill({"skillText": "x" * (ws.MAX_SKILL_CHARS + 1)})


class ExecutorPromptTests(unittest.TestCase):
    FIELDS = [{"name": "customer", "type": "text", "required": True,
               "description": "The customer name"},
              {"name": "amount", "type": "number", "description": "Total"}]

    def _prompt_for(self, config):
        seen = {}

        def ai_call(prompt, system):
            seen["prompt"] = prompt
            seen["system"] = system
            return '{"customer": "Acme", "amount": 5}'

        result = AIExtractExecutor(ai_call).execute(config, "Acme owes 5")
        self.assertTrue(result["success"], result)
        return seen

    def test_no_skill_key_is_byte_identical(self):
        base = {"fields": self.FIELDS, "special_instructions": "Numbers without symbols"}
        before = self._prompt_for(dict(base))
        with_empty = self._prompt_for(dict(base, skill_instructions=""))
        self.assertEqual(before["prompt"], with_empty["prompt"])
        self.assertEqual(before["system"], with_empty["system"])
        self.assertNotIn("SKILL GUIDANCE", before["prompt"])

    def test_skill_block_sits_between_schema_and_special_instructions(self):
        seen = self._prompt_for({"fields": self.FIELDS,
                                 "special_instructions": "Numbers without symbols",
                                 "skill_instructions": "Finance owns chargebacks."})
        p = seen["prompt"]
        self.assertEqual(p.count("SKILL GUIDANCE"), 1)
        self.assertIn("Finance owns chargebacks.", p)
        self.assertLess(p.index("EXPECTED OUTPUT STRUCTURE"), p.index("SKILL GUIDANCE"))
        self.assertLess(p.index("SKILL GUIDANCE"), p.index("SPECIAL INSTRUCTIONS"))
        self.assertLess(p.index("SPECIAL INSTRUCTIONS"), p.index("CONTENT TO EXTRACT FROM"))
        self.assertNotIn("SKILL GUIDANCE", seen["system"])       # system message untouched

    def test_legacy_positional_prompt_call_still_works(self):
        ex = AIExtractExecutor(lambda p, s: "{}")
        p = ex._build_field_extraction_prompt(self.FIELDS, "", "content", "")
        self.assertNotIn("SKILL GUIDANCE", p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
