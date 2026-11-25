"""The Agent's document tools return document text WHOLE (agent_service/document_tools.py).

search_documents used to cut every passage at 4,000 characters without saying so, and to
show 12 passages whatever their size: an answer further down a long page, or on the 15th
page that matched, was simply not in the result. Both are gone.

- A passage is a whole page, to its last character.
- One search returns everything that matched, best first, as far as two limits that are
  not ours allow: what one tool result can carry, and a quarter of the model's context
  window. It states when more matched, and the model reads on with start_at from the SAME
  list (kept per user, conversation and question).
- The only hard limit is the runtime's (how much one tool result can carry). The tools
  declare its maximum, and whatever still does not fit is reported, never cut silently.

Needs the agent env (claude_agent_sdk); self-skips elsewhere. Standalone, with that
env's python: tests_v2/unit/test_agent_document_result_size.py
"""
import asyncio
import os
import re
import sys
import tempfile
import unittest

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import document_tools as dt                       # noqa: E402
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


def _run(tool_obj, args):
    handler = getattr(tool_obj, "handler", tool_obj)
    return asyncio.run(handler(args))


def _text_of(result):
    blocks = (result or {}).get("content") or []
    return " ".join(b.get("text", "") for b in blocks if isinstance(b, dict))


FULL_WINDOW = 1_000_000        # the current models
OLDER_WINDOW = 200_000         # the switch off, or an older model: a search takes 130,000 characters


def _has_page(text, n):
    """True when page n's own last word is in the text, i.e. the page is there to its end."""
    return re.search(rf"END-OF-PAGE-{n}(?!\d)", text) is not None


def _page(n, chars, marker=None):
    """One search passage: a page of `chars` characters that ends with its own marker."""
    marker = marker or f"END-OF-PAGE-{n}"
    filler = ("word " * (chars // 5 + 1))[:max(0, chars - len(marker) - 1)]
    return {"filename": "Vendor Guide.pdf", "page": n, "document_id": "doc-1",
            "document_type": "vendor_guide", "text": f"{filler} {marker}", "fields": {}}


@unittest.skipUnless(HAVE_SDK, "document_tools not importable in this env")
class _SearchCase(unittest.TestCase):
    USER = {"user_id": 987654, "username": "size-test", "role": 2, "name": "Size Test",
            "session_id": "conversation-1"}
    KEY = ("987654", "conversation-1")

    def setUp(self):
        self._saved_post, self._saved_note = dt._post_main, dt._knowledge_agents_note
        self._saved_max = dt.RESULT_MAX_CHARS
        self._saved_keep = (dt._KEPT_SEARCH_SECONDS, dt._KEPT_SEARCH_TOTAL_CHARS)
        self._saved_env = os.environ.pop("AGENT_DOC_SEARCH_RESULT_CHARS", None)
        self._saved_window = dt._context_window_tokens
        self.window(FULL_WINDOW)              # what a turn has by default
        dt._kept_searches.clear()
        self._token = CURRENT_USER.set(dict(self.USER))
        self.searches = 0                     # how many times the library was really searched

        async def no_note():
            return ""
        dt._knowledge_agents_note = no_note

    def window(self, tokens):
        """The context window of the model the turn runs on."""
        dt._context_window_tokens = lambda: tokens

    def tearDown(self):
        dt._post_main, dt._knowledge_agents_note = self._saved_post, self._saved_note
        dt._context_window_tokens = self._saved_window
        dt.RESULT_MAX_CHARS = self._saved_max
        dt._KEPT_SEARCH_SECONDS, dt._KEPT_SEARCH_TOTAL_CHARS = self._saved_keep
        os.environ.pop("AGENT_DOC_SEARCH_RESULT_CHARS", None)
        if self._saved_env is not None:
            os.environ["AGENT_DOC_SEARCH_RESULT_CHARS"] = self._saved_env
        dt._kept_searches.clear()
        CURRENT_USER.reset(self._token)

    def _serve(self, passages, **extra):
        async def fake_post(path, body, internal=False, read_timeout=120.0):
            self.searches += 1
            return ({"status": "success",
                     "result": {"ok": True, "passages": passages, "query_analysis": {}, **extra}}, 200)
        dt._post_main = fake_post

    def _search(self, **args):
        args.setdefault("query", "q")
        return _text_of(_run(dt.search_documents, args))


class TestPassagesAreWhole(_SearchCase):

    def test_a_long_page_is_returned_to_its_last_character(self):
        self._serve([_page(19, 9_000)])
        text = self._search(query="split purchase orders")
        self.assertIn("END-OF-PAGE-19", text)          # 4,000 used to be the end of it
        self.assertGreater(len(text), 8_900)

    def test_an_answer_just_past_4000_characters_is_there(self):
        # the case that was found: the answer sat about 4,010 characters into the passage
        page = _page(19, 4_530)
        page["text"] = page["text"][:4_010] + " ANSWER-HERE approximately 45 days " + page["text"][4_010:]
        self._serve([page])
        self.assertIn("ANSWER-HERE approximately 45 days", self._search())

    def test_no_setting_brings_the_cut_back(self):
        os.environ["AGENT_DOC_PASSAGE_CHARS"] = "100"
        try:
            self._serve([_page(1, 6_000)])
            self.assertIn("END-OF-PAGE-1", self._search())
        finally:
            os.environ.pop("AGENT_DOC_PASSAGE_CHARS", None)

    def test_an_answer_the_engine_wrote_itself_is_whole_too(self):
        self._serve([], answer=("sentence " * 900) + "LAST-WORD")   # about 8,100 characters
        self.assertIn("LAST-WORD", self._search())


class TestHowMuchOneSearchReturns(_SearchCase):
    """No fixed number of passages: the best-ranked ones up to the usual size of one result."""

    def test_forty_ordinary_pages_are_all_returned(self):
        # 40 pages of 800 characters: 12 used to be all the model ever saw of them
        self._serve([_page(n, 800) for n in range(1, 41)])
        text = self._search()
        self.assertIn("40 passage(s) for", text)
        self.assertTrue(_has_page(text, 40))
        self.assertNotIn("MORE MATCHED", text)

    def test_the_answer_on_the_24th_page_that_matched_is_read(self):
        # measured case: 38 pages matched, about 2,800 characters each, the answer on the 24th
        pages = [_page(n, 2_800) for n in range(1, 39)]
        pages[23]["text"] += " the trailer floor must be at least 50 inches high"
        self._serve(pages)
        self.assertIn("the trailer floor must be at least 50 inches high", self._search())

    def test_with_the_full_context_window_everything_that_matched_comes_back(self):
        # 130 pages of 3,500 characters = 455,000: 12 pages, then 100,000 characters, used to be all
        self._serve([_page(n, 3_500) for n in range(1, 131)])
        text = self._search()
        self.assertIn("130 passage(s) for", text)
        self.assertTrue(_has_page(text, 1) and _has_page(text, 130))
        self.assertNotIn("MORE MATCHED", text)

    def test_with_the_full_window_the_only_limit_is_what_one_result_carries(self):
        self._serve([_page(n, 20_000) for n in range(1, 61)])     # 1.2 million characters matched
        text = self._search()
        shown = [n for n in range(1, 61) if _has_page(text, n)]
        self.assertEqual(shown, list(range(1, len(shown) + 1)))   # in rank order, none cut in half
        self.assertEqual(len(shown), 24)                          # 24 x 20,000 fit in 495,000
        self.assertLessEqual(len(text), dt.RESULT_MAX_CHARS)
        self.assertIn("Passages 1 to 24 of 60", text)
        self.assertIn("MORE MATCHED, NOT SHOWN: 36 further passage(s)", text)
        self.assertIn("start_at=25", text)
        self.assertIn("before you reword the search or tell the user the documents do not say", text)

    def test_on_a_200000_token_window_one_search_takes_a_quarter_of_it(self):
        self.window(OLDER_WINDOW)
        self._serve([_page(n, 5_000) for n in range(1, 41)])      # 200,000 characters matched
        text = self._search()
        shown = [n for n in range(1, 41) if _has_page(text, n)]
        self.assertEqual(shown, list(range(1, 26)))               # 25 x 5,000 fit in 130,000
        self.assertLessEqual(len(text), 131_000)
        self.assertIn("Passages 1 to 25 of 40", text)
        self.assertIn("MORE MATCHED, NOT SHOWN: 15 further passage(s)", text)
        self.assertIn("start_at=26", text)

    def test_the_size_follows_the_window_of_the_model_the_user_runs_on(self):
        self.assertEqual(dt._default_search_chars(), 650_000)     # a quarter of 1,000,000 tokens
        self.window(OLDER_WINDOW)
        self.assertEqual(dt._default_search_chars(), 130_000)     # a quarter of 200,000 tokens
        # the real lookup: the model of the current user's role, and that model's window
        import agent_config
        dt._context_window_tokens = self._saved_window
        saved = agent_config.get_effective_model, agent_config.context_window_tokens
        seen = []
        agent_config.get_effective_model = lambda role=None: seen.append(role) or f"model-for-role-{role}"
        agent_config.context_window_tokens = lambda model: 200_000 if model == "model-for-role-1" else 1_000_000
        try:
            self.assertEqual(dt._context_window_tokens(), 1_000_000)          # the test user is role 2
            one = CURRENT_USER.set(dict(self.USER, role=1))
            try:
                self.assertEqual(dt._context_window_tokens(), 200_000)
            finally:
                CURRENT_USER.reset(one)
            self.assertEqual(seen, [2, 1])

            def broken(role=None):
                raise RuntimeError("settings unreadable")
            agent_config.get_effective_model = broken
            self.assertEqual(dt._context_window_tokens(), 200_000)            # the runtime's own default
        finally:
            agent_config.get_effective_model, agent_config.context_window_tokens = saved

    def test_a_fixed_size_can_be_set_instead(self):
        self._serve([_page(n, 5_000) for n in range(1, 41)])
        os.environ["AGENT_DOC_SEARCH_RESULT_CHARS"] = "20000"
        self.assertIn("Passages 1 to 3 of 40", self._search())
        os.environ["AGENT_DOC_SEARCH_RESULT_CHARS"] = "100000"
        self.assertIn("Passages 1 to 19 of 40", self._search())
        self.window(OLDER_WINDOW)
        os.environ["AGENT_DOC_SEARCH_RESULT_CHARS"] = "400000"                # larger than the window rule
        self.assertIn("40 passage(s) for", self._search())
        for not_a_size in ("not a number", "0", "-5"):
            os.environ["AGENT_DOC_SEARCH_RESULT_CHARS"] = not_a_size
            self.assertIn("MORE MATCHED, NOT SHOWN: 15", self._search(), not_a_size)   # the window rule again

    def test_a_setting_above_what_one_result_carries_is_held_to_what_it_carries(self):
        os.environ["AGENT_DOC_SEARCH_RESULT_CHARS"] = "9000000"
        self._serve([_page(n, 20_000) for n in range(1, 61)])     # 1.2 million characters
        text = self._search()
        self.assertLessEqual(len(text), dt.RESULT_MAX_CHARS)
        self.assertIn("MORE MATCHED, NOT SHOWN", text)

    def test_the_first_page_is_whole_even_when_it_alone_is_over_the_usual_size(self):
        self.window(OLDER_WINDOW)                                 # 130,000 characters a search
        self._serve([_page(1, 180_000), _page(2, 800)])
        text = self._search()
        self.assertTrue(_has_page(text, 1))
        self.assertNotIn("THIS PAGE IS CUT HERE", text)
        self.assertIn("Passages 1 to 1 of 2", text)

    def test_a_number_asked_for_is_that_many(self):
        self._serve([_page(n, 5_000) for n in range(1, 91)])
        text = self._search(max_results=90)                       # 450,000 characters, 50 used to be the most
        self.assertIn("90 passage(s) for", text)
        self.assertTrue(_has_page(text, 90))
        text = self._search(max_results=5)
        self.assertIn("Passages 1 to 5 of 90", text)
        self.assertFalse(_has_page(text, 6))
        self.assertIn("MORE MATCHED, NOT SHOWN: 85", text)

    def test_all_shown_says_nothing_about_more(self):
        self._serve([_page(n, 800) for n in range(1, 6)])
        text = self._search()
        self.assertIn("5 passage(s) for", text)
        self.assertNotIn("MORE MATCHED", text)
        self.assertNotIn("NOT ALL SHOWN", text)

    def test_the_same_page_twice_counts_once(self):
        self._serve([_page(3, 800), _page(3, 800), _page(4, 800)])
        self.assertIn("2 passage(s) for", self._search())


class TestReadingOn(_SearchCase):
    """start_at continues the list the model saw the start of, not a second search."""

    def test_reading_on_continues_the_same_list_without_searching_again(self):
        self.window(OLDER_WINDOW)
        self._serve([_page(n, 5_000) for n in range(1, 41)])
        first = self._search(query="Trailer height?")
        shown = len([n for n in range(1, 41) if _has_page(first, n)])
        # the engine plans every search afresh: a second run would come back different
        self._serve([_page(n, 5_000) for n in range(500, 503)])
        more = self._search(query="  trailer   HEIGHT? ", start_at=shown + 1)
        self.assertEqual(self.searches, 1)                         # not searched a second time
        self.assertIn(f"Passages {shown + 1} to ", more)
        self.assertTrue(_has_page(more, shown + 1))
        self.assertFalse(_has_page(more, shown))                   # nothing repeated
        self.assertFalse(_has_page(more, 500))
        self.assertNotIn("NOTE: no earlier search", more)
        # that was the rest of the list, and asking past its end says so
        self.assertTrue(_has_page(more, 40))
        self.assertNotIn("MORE MATCHED", more)
        past = self._search(query="trailer height?", start_at=41)
        self.assertIn("You have seen them all", past)
        self.assertEqual(self.searches, 1)

    def test_every_page_is_read_exactly_once_across_the_parts(self):
        self.window(OLDER_WINDOW)
        self._serve([_page(n, 7_300) for n in range(1, 61)])
        seen, start = [], 1
        for _ in range(10):
            text = self._search(start_at=start) if start > 1 else self._search()
            part = [n for n in range(1, 61) if _has_page(text, n)]
            seen += part
            if "MORE MATCHED" not in text:
                break
            start = part[-1] + 1
        self.assertEqual(seen, list(range(1, 61)))
        self.assertEqual(self.searches, 1)

    def test_a_number_and_a_position_together(self):
        self._serve([_page(n, 800) for n in range(1, 41)])
        self._search()
        text = self._search(start_at=13, max_results=10)
        self.assertIn("Passages 13 to 22 of 40", text)
        self.assertTrue(_has_page(text, 13) and _has_page(text, 22))
        self.assertFalse(_has_page(text, 12) or _has_page(text, 23))
        self.assertIn("start_at=23", text)

    def test_a_position_past_the_end_of_the_list_says_all_were_seen(self):
        self._serve([_page(n, 800) for n in range(1, 6)])
        self._search()
        text = self._search(start_at=9)
        self.assertIn("Only 5 passage(s) matched", text)
        self.assertIn("You have seen them all", text)

    def test_reading_on_with_other_words_continues_the_last_search_and_says_so(self):
        # measured: the model added a word to its query when it read on. start_at can only
        # mean "after the passages I was shown", so it is the same list, searched once.
        self._serve([_page(n, 5_000) for n in range(1, 41)])
        self._search(query="charge for shipping to the incorrect DC", max_results=9)
        self._serve([_page(n, 800) for n in range(500, 503)])
        text = self._search(query="vendor charge for shipping to the incorrect DC", start_at=10, max_results=9)
        self.assertEqual(self.searches, 1)
        self.assertTrue(text.startswith('NOTE: this reads on in your last search, '
                                        '"charge for shipping to the incorrect DC"'), text[:200])
        self.assertIn('"vendor charge for shipping to the incorrect DC" was not searched', text)
        self.assertIn('Passages 10 to 18 of 40 for "charge for shipping to the incorrect DC"', text)
        self.assertTrue(_has_page(text, 10) and _has_page(text, 18))
        self.assertFalse(_has_page(text, 500))

    def test_other_words_read_on_in_the_LAST_search_of_the_conversation(self):
        self._serve([_page(n, 800) for n in range(1, 41)])
        self._search(query="first subject", max_results=5)
        self._serve([_page(n, 800) for n in range(100, 141)])
        self._search(query="second subject", max_results=5)
        text = self._search(query="second subject reworded", start_at=6, max_results=5)
        self.assertTrue(_has_page(text, 105))
        # the earlier one is still read on in by its own words
        text = self._search(query="first subject", start_at=6, max_results=5)
        self.assertTrue(_has_page(text, 6))
        self.assertNotIn("NOTE:", text)
        self.assertEqual(self.searches, 2)

    def test_another_conversation_of_the_same_user_does_not_read_on_in_this_one(self):
        self._serve([_page(n, 800) for n in range(1, 41)])
        self._search(query="payment terms", max_results=5)
        other = CURRENT_USER.set(dict(self.USER, session_id="conversation-2"))
        try:
            text = self._search(query="payment terms reworded", start_at=6, max_results=5)
        finally:
            CURRENT_USER.reset(other)
        self.assertEqual(self.searches, 2)
        self.assertTrue(text.startswith("NOTE: no earlier search of this conversation is kept"))

    def test_without_a_conversation_only_the_same_words_read_on(self):
        nowhere = CURRENT_USER.set(dict(self.USER, session_id=None))     # a run that has no conversation id
        try:
            self._serve([_page(n, 800) for n in range(1, 41)])
            self._search(query="payment terms", max_results=5)
            same = self._search(query="Payment  terms", start_at=6, max_results=5)
            self.assertEqual(self.searches, 1)
            self.assertNotIn("NOTE:", same)
            other = self._search(query="payment terms reworded", start_at=6, max_results=5)
            self.assertEqual(self.searches, 2)
            self.assertTrue(other.startswith("NOTE: no earlier search of this conversation is kept"))
        finally:
            CURRENT_USER.reset(nowhere)

    def test_reading_on_when_nothing_was_searched_runs_the_search_and_says_so(self):
        self._serve([_page(n, 800) for n in range(1, 41)])
        text = self._search(query="a first question", start_at=13, max_results=10)
        self.assertEqual(self.searches, 1)
        self.assertTrue(text.startswith("NOTE: no earlier search of this conversation is kept"))
        self.assertIn("its first 12 passage(s) are not shown", text)
        self.assertIn("Passages 13 to 22 of 40", text)

    def test_a_position_past_the_end_of_a_search_run_just_now_does_not_claim_all_were_seen(self):
        self._serve([_page(n, 800) for n in range(1, 6)])
        text = self._search(query="a first question", start_at=9)
        self.assertNotIn("You have seen them all", text)
        self.assertIn("this one was run now and matched 5 passage(s)", text)
        self.assertIn("leave start_at out", text)

    def test_past_the_end_with_other_words_names_the_search_it_read_on_in(self):
        self._serve([_page(n, 800) for n in range(1, 6)])
        self._search(query="trailer height")
        text = self._search(query="trailer lip height", start_at=6)
        self.assertEqual(self.searches, 1)
        self.assertIn('Only 5 passage(s) matched "trailer height"', text)
        self.assertIn('To search for "trailer lip height", leave start_at out', text)

    def test_one_users_list_is_never_read_on_by_another(self):
        self._serve([_page(n, 5_000) for n in range(1, 41)])
        self._search(query="payment terms")
        other = CURRENT_USER.set({"user_id": 555001, "username": "someone-else", "role": 2,
                                  "session_id": "conversation-1"})
        try:
            self._serve([_page(n, 800) for n in range(700, 703)])
            text = self._search(query="payment terms", start_at=2)
        finally:
            CURRENT_USER.reset(other)
        self.assertEqual(self.searches, 2)                         # their own search, with their own access
        self.assertTrue(_has_page(text, 701))
        self.assertFalse(_has_page(text, 2))
        self.assertTrue(text.startswith("NOTE: no earlier search of this conversation is kept"))

    def test_nothing_is_kept_for_the_service_itself(self):
        nobody = CURRENT_USER.set({"user_id": 0, "username": "service", "role": 3})
        try:
            self._serve([_page(n, 800) for n in range(1, 41)])
            self._search()
            self.assertEqual(dt._kept_searches, {})
            self._search(start_at=5)
        finally:
            CURRENT_USER.reset(nobody)
        self.assertEqual(self.searches, 2)

    def test_a_list_is_kept_for_a_while_then_the_search_runs_again(self):
        self._serve([_page(n, 800) for n in range(1, 41)])
        self._search(max_results=5)
        self._search(start_at=6, max_results=5)
        self.assertEqual(self.searches, 1)
        key = self.KEY + ("q",)
        when, found, size = dt._kept_searches[key]                 # as if the time had passed
        dt._kept_searches[key] = (when - dt._KEPT_SEARCH_SECONDS - 1, found, size)
        text = self._search(start_at=11, max_results=5)
        self.assertEqual(self.searches, 2)
        self.assertIn("NOTE: no earlier search of this conversation is kept", text)

    def test_a_new_search_of_the_same_question_replaces_the_list(self):
        self._serve([_page(n, 800) for n in range(1, 41)])
        self._search(max_results=5)
        self._serve([_page(n, 800) for n in range(300, 320)])
        self._search(max_results=5)                                # asked again from the start
        self.assertEqual(self.searches, 2)
        text = self._search(start_at=6, max_results=5)
        self.assertTrue(_has_page(text, 305))                      # reads on in the latest one
        self.assertIn("of 20", text)

    def test_kept_lists_stay_inside_their_memory_and_the_oldest_goes_first(self):
        dt._KEPT_SEARCH_TOTAL_CHARS = 100_000
        self._serve([_page(n, 5_000) for n in range(1, 13)])       # about 60,000 characters each
        self._search(query="first question")
        self._search(query="second question")
        self.assertEqual(list(dt._kept_searches), [self.KEY + ("second question",)])
        # the one that went is simply searched again
        self._search(query="first question")
        self.assertEqual(self.searches, 3)

    def test_one_list_larger_than_the_memory_is_still_kept_for_its_own_reading_on(self):
        self.window(OLDER_WINDOW)
        dt._KEPT_SEARCH_TOTAL_CHARS = 10_000
        self._serve([_page(n, 5_000) for n in range(1, 41)])
        first = self._search()
        shown = len([n for n in range(1, 41) if _has_page(first, n)])
        self._search(start_at=shown + 1)
        self.assertEqual(self.searches, 1)


class TestWhatDoesNotFitIsSaid(_SearchCase):

    def test_the_tools_declare_the_runtime_maximum(self):
        self.assertEqual(dt.RESULT_MAX_CHARS, 500_000)
        for t in (dt.search_documents, dt.read_file):
            self.assertEqual(getattr(t.annotations, "maxResultSizeChars", None), 500_000, t.name)

    def test_passages_asked_for_that_do_not_fit_are_left_out_whole_and_counted(self):
        # 60 pages of 20,000 characters = 1.2 million: more than one result carries
        self._serve([_page(n, 20_000) for n in range(1, 61)])
        text = self._search(max_results=60)
        self.assertLessEqual(len(text), dt.RESULT_MAX_CHARS)
        shown = [n for n in range(1, 61) if _has_page(text, n)]
        self.assertGreaterEqual(len(shown), 20)
        self.assertEqual(shown, list(range(1, len(shown) + 1)))       # in order, none cut in half
        self.assertIn(f"Passages 1 to {len(shown)} of 60", text)
        self.assertIn(f"NOT ALL SHOWN: {60 - len(shown)} of the 60 passages you asked for did not fit", text)
        self.assertIn(f"start_at={len(shown) + 1}", text)
        self.assertIn("nothing was cut inside a passage", text)

    def test_without_the_declaration_the_result_stays_inside_the_runtime_default(self):
        dt.RESULT_MAX_CHARS = 50_000          # an SDK that cannot declare a larger size
        self._serve([_page(n, 6_000) for n in range(1, 13)])
        text = self._search()
        self.assertLessEqual(len(text), 50_000)        # over it, the runtime shows a 2 KB preview
        self.assertTrue(_has_page(text, 7))             # 7 whole pages of 6,000 fit in 45,000
        self.assertFalse(_has_page(text, 8))
        self.assertIn("MORE MATCHED, NOT SHOWN: 5 further passage(s)", text)
        self.assertIn("start_at=8", text)

    def test_one_page_larger_than_a_whole_result_says_where_it_stops(self):
        self._serve([_page(1, 700_000), _page(2, 800)])
        text = self._search()
        self.assertLessEqual(len(text), dt.RESULT_MAX_CHARS)
        self.assertIn("THIS PAGE IS CUT HERE: it has 7", text)
        self.assertFalse(_has_page(text, 1))
        self.assertIn("start_at=2", text)              # the next page can still be read

    def test_the_notes_after_the_passages_are_never_squeezed_out(self):
        async def note():
            return "\n\nNOT SEARCHED HERE — agents that hold their own knowledge documents: Compliance"
        dt._knowledge_agents_note = note
        self._serve([_page(n, 20_000) for n in range(1, 61)], records_hint="NOTE: structured record rows exist",
                    ambiguity_hint="SEVERAL DOCUMENTS MATCH")
        for args in ({"max_results": 60}, {}, {"start_at": 4}):
            text = self._search(**args)
            self.assertIn("NOT SEARCHED HERE", text, args)
            self.assertIn("NOTE: structured record rows exist", text, args)
            self.assertIn("SEVERAL DOCUMENTS MATCH", text, args)
            self.assertLessEqual(len(text), dt.RESULT_MAX_CHARS, args)

    def test_the_search_confidence_is_still_reported_when_reading_on(self):
        async def fake_post(path, body, internal=False, read_timeout=120.0):
            return ({"status": "success", "result": {"ok": True, "passages": [_page(n, 800) for n in range(1, 9)],
                                                     "query_analysis": {"confidence": "high"}}}, 200)
        dt._post_main = fake_post
        self.assertIn("search confidence: high", self._search(max_results=3))
        self.assertIn("search confidence: high", self._search(start_at=4))


@unittest.skipUnless(HAVE_SDK, "document_tools not importable in this env")
class TestReadFileIsWhole(unittest.TestCase):
    def setUp(self):
        self._saved_resolve, self._saved_max = dt._resolve_read_path, dt.RESULT_MAX_CHARS
        self._dir = tempfile.TemporaryDirectory()
        self._token = CURRENT_USER.set({"user_id": 987654, "username": "size-test", "role": 3, "name": "T"})
        dt._resolve_read_path = lambda raw: (raw, None)

    def tearDown(self):
        dt._resolve_read_path, dt.RESULT_MAX_CHARS = self._saved_resolve, self._saved_max
        CURRENT_USER.reset(self._token)
        self._dir.cleanup()

    def _file(self, chars):
        path = os.path.join(self._dir.name, "notes.txt")
        lines, size, i = [], 0, 0
        while size < chars:
            i += 1
            ln = f"line {i:07d} of the notes"
            lines.append(ln)
            size += len(ln) + 1
        lines.append("THE-LAST-LINE")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines))
        return path, i

    def test_a_file_of_120000_characters_comes_back_whole(self):
        path, _n = self._file(120_000)       # over the runtime's default of 50,000
        text = _text_of(_run(dt.read_file, {"path": path}))
        self.assertIn("THE-LAST-LINE", text)
        self.assertIn("line 0000001 of the notes", text)
        self.assertNotIn("NOT ALL SHOWN", text)

    def test_a_file_longer_than_one_result_is_read_in_parts_and_says_so(self):
        path, last = self._file(1_200_000)
        first = _text_of(_run(dt.read_file, {"path": path}))
        self.assertLessEqual(len(first), dt.RESULT_MAX_CHARS)
        self.assertIn("characters 1 to 495,000 of", first)
        self.assertIn("NOT ALL SHOWN", first)
        self.assertIn("start=495000", first)
        self.assertNotIn("THE-LAST-LINE", first)
        second = _text_of(_run(dt.read_file, {"path": path, "start": 495_000}))
        third = _text_of(_run(dt.read_file, {"path": path, "start": 990_000}))
        self.assertIn("THE-LAST-LINE", third)
        self.assertIn("That is the end of the file", third)
        # the three parts together are the file: no line lost at the joins
        body = lambda t: t.split("```\n", 1)[1].rsplit("\n```", 1)[0]          # noqa: E731
        whole = body(first) + body(second) + body(third)
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(whole, fh.read())
        self.assertIn(f"line {last:07d} of the notes", whole)

    def test_a_start_past_the_end_says_so(self):
        path, _n = self._file(2_000)
        text = _text_of(_run(dt.read_file, {"path": path, "start": 9_999_999}))
        self.assertIn("You have read it all", text)

    def test_a_short_file_reads_exactly_as_before(self):
        path, _n = self._file(300)
        text = _text_of(_run(dt.read_file, {"path": path}))
        self.assertTrue(text.startswith("Contents of 'notes.txt' ("))
        self.assertIn("):\n```\n", text)
        self.assertNotIn("characters 1 to", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
