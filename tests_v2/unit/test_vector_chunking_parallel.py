"""Search index: the pages of a document are split into chunks several at a time.

With smart chunking every page longer than one chunk costs one AI call of several seconds.
Made one after another those calls were 89 to 98% of the time a document took to index
(LLMDocumentVectorEngine.ChromaDBStore.add_documents). They are now made several at a time
(VECTOR_CHUNKING_PARALLEL). What must hold:

- the chunks reach the index in the order of the pages, with the same ids as before;
- 1 = one after another, exactly as before;
- a page whose AI call fails (the chunker then splits it the standard way) is tried once
  more, and what is still split without AI is counted and reported, never silent.

No AI and no index here: the chunker and the collection are stand-ins.
"""
from __future__ import annotations

import threading
import time

import pytest

engine_mod = pytest.importorskip("LLMDocumentVectorEngine")
cfg = engine_mod.cfg


class FakeChunker:
    """Stands in for TextChunker: 'AI' chunks for long pages, standard ones when told to fail."""
    chunk_size = 20
    _use_smart = True

    def __init__(self, seconds=0.05, fail=None, boom=None):
        self.seconds = seconds
        self.fail = dict(fail or {})        # page text -> how many times its AI call fails
        self.boom = boom                    # page text that raises
        self.calls = []
        self.running = 0
        self.most_at_once = 0
        self._lock = threading.Lock()

    def chunk_text(self, text, metadata=None):
        with self._lock:
            self.calls.append(text)
            self.running += 1
            self.most_at_once = max(self.most_at_once, self.running)
        try:
            if text == self.boom:
                raise RuntimeError("chunker broke")
            long_page = len(text.strip()) > self.chunk_size
            if long_page:
                time.sleep(self.seconds)
            with self._lock:
                failed = self.fail.get(text, 0) > 0
                if failed:
                    self.fail[text] -= 1
            kind = "llm" if (self._use_smart and not failed) else "recursive_character"
            halves = [text[:len(text) // 2], text[len(text) // 2:]] if long_page else [text]
            return [{"text": part, "metadata": dict(metadata or {}, chunk_index=i, total_chunks=len(halves),
                                                    splitter_type=kind)}
                    for i, part in enumerate(halves)]
        finally:
            with self._lock:
                self.running -= 1


class FakeCollection:
    def __init__(self):
        self.added = None

    def add(self, documents, metadatas, ids):
        self.added = {"documents": documents, "metadatas": metadatas, "ids": ids}


def _pages(n):
    return ([f"page {i:03d} " + "words " * 12 for i in range(1, n + 1)],
            [{"page_number": i} for i in range(1, n + 1)],
            [f"doc_p{i}" for i in range(1, n + 1)])


@pytest.fixture
def store(monkeypatch):
    s = engine_mod.ChromaDBStore()
    s.collection = FakeCollection()
    s.chunker = FakeChunker()
    monkeypatch.setattr(cfg, "VECTOR_ENABLE_CHUNKING", True, raising=False)
    monkeypatch.setattr(cfg, "VECTOR_CHUNKING_PARALLEL", 8, raising=False)
    return s


class TestSeveralAtATime:

    def test_the_chunks_reach_the_index_in_page_order_whatever_finishes_first(self, store):
        docs, metas, ids = _pages(24)

        # the first pages take longest, so they finish LAST
        real = store.chunker.chunk_text

        def slow_first(text, metadata=None):
            if text in docs[:4]:
                time.sleep(0.25)
            return real(text, metadata)
        store.chunker.chunk_text = slow_first
        store.add_documents(docs, metas, ids)
        added = store.collection.added
        assert added["ids"] == [f"doc_p{i}_chunk_{c}" for i in range(1, 25) for c in (0, 1)]
        assert [m["page_number"] for m in added["metadatas"]] == [i for i in range(1, 25) for _ in (0, 1)]
        assert [m["original_doc_id"] for m in added["metadatas"]] == [f"doc_p{i}" for i in range(1, 25) for _ in (0, 1)]
        assert all(m["full_text"] == docs[m["page_number"] - 1] for m in added["metadatas"])
        assert "".join(added["documents"]) == "".join(docs)          # nothing lost, nothing doubled

    def test_it_is_the_same_result_as_one_after_another(self, store, monkeypatch):
        docs, metas, ids = _pages(12)
        store.add_documents(docs, metas, ids)
        several = store.collection.added
        monkeypatch.setattr(cfg, "VECTOR_CHUNKING_PARALLEL", 1, raising=False)
        store.chunker = FakeChunker()
        store.add_documents(docs, metas, ids)
        assert store.collection.added == several

    def test_the_calls_really_overlap_and_the_time_shows_it(self, store):
        docs, metas, ids = _pages(16)
        store.chunker = FakeChunker(seconds=0.2)
        t0 = time.perf_counter()
        store.add_documents(docs, metas, ids)
        took = time.perf_counter() - t0
        assert store.chunker.most_at_once == 8                # the setting
        assert took < 1.6                                     # 16 x 0.2 s in a row would be 3.2 s
        assert store.last_chunking()["parallel"] == 8

    def test_one_means_one_after_another(self, store, monkeypatch):
        monkeypatch.setattr(cfg, "VECTOR_CHUNKING_PARALLEL", 1, raising=False)
        docs, metas, ids = _pages(6)
        store.add_documents(docs, metas, ids)
        assert store.chunker.most_at_once == 1
        assert store.chunker.calls == docs                    # in order
        assert store.last_chunking()["parallel"] == 1

    @pytest.mark.parametrize("setting, expected", [(16, 16), ("12", 12), (0, 1), (None, 1), ("many", 8), (500, 40)])
    def test_the_setting(self, store, monkeypatch, setting, expected):
        monkeypatch.setattr(cfg, "VECTOR_CHUNKING_PARALLEL", setting, raising=False)
        docs, metas, ids = _pages(40)
        store.chunker = FakeChunker(seconds=0.02)
        store.add_documents(docs, metas, ids)
        assert store.last_chunking()["parallel"] == expected  # never more threads than pages

    def test_a_single_page_needs_no_threads(self, store):
        docs, metas, ids = _pages(1)
        store.add_documents(docs, metas, ids)
        assert store.last_chunking()["parallel"] == 1
        assert store.collection.added["ids"] == ["doc_p1_chunk_0", "doc_p1_chunk_1"]


class TestAFailedAICallIsNeverSilent:

    def test_a_page_that_fails_once_is_tried_again_and_ends_split_by_ai(self, store):
        docs, metas, ids = _pages(10)
        store.chunker = FakeChunker(fail={docs[3]: 1})
        store.add_documents(docs, metas, ids)
        stats = store.last_chunking()
        assert (stats["split_by_ai"], stats["retried"], stats["split_without_ai"]) == (10, 1, 0)
        assert store.chunker.calls.count(docs[3]) == 2
        kinds = {m["page_number"]: m["splitter_type"] for m in store.collection.added["metadatas"]}
        assert set(kinds.values()) == {"llm"}

    def test_a_page_that_keeps_failing_is_indexed_the_standard_way_and_reported(self, store, caplog):
        docs, metas, ids = _pages(10)
        store.chunker = FakeChunker(fail={docs[3]: 5, docs[7]: 5})
        with caplog.at_level("WARNING"):
            store.add_documents(docs, metas, ids)
        stats = store.last_chunking()
        assert (stats["split_by_ai"], stats["retried"], stats["split_without_ai"]) == (8, 2, 2)
        assert stats["split_without_ai_ids"] == ["doc_p4", "doc_p8"]
        assert store.chunker.calls.count(docs[3]) == 2        # one more try, not an endless loop
        # the page is still in the index: a recoverable gap does not fail the document
        assert len(store.collection.added["ids"]) == 20
        assert any("AI call failed twice for 2 document(s)" in r.message and "doc_p4" in r.message
                   for r in caplog.records)

    def test_short_pages_need_no_ai_and_are_not_counted_as_failures(self, store):
        docs = ["short one", "short two"] + _pages(3)[0]
        metas = [{"page_number": i} for i in range(1, 6)]
        ids = [f"doc_p{i}" for i in range(1, 6)]
        chunker = FakeChunker()
        real = chunker.chunk_text

        def short_pages_are_single_standard_chunks(text, metadata=None):
            out = real(text, metadata)
            if len(text) <= chunker.chunk_size:
                out[0]["metadata"]["splitter_type"] = "recursive_character"
            return out
        chunker.chunk_text = short_pages_are_single_standard_chunks
        store.chunker = chunker
        store.add_documents(docs, metas, ids)
        stats = store.last_chunking()
        assert (stats["documents"], stats["split_by_ai"], stats["retried"], stats["split_without_ai"]) == (5, 3, 0, 0)

    def test_with_smart_chunking_off_nothing_is_retried(self, store):
        docs, metas, ids = _pages(6)
        store.chunker = FakeChunker()
        store.chunker._use_smart = False
        store.add_documents(docs, metas, ids)
        stats = store.last_chunking()
        assert (stats["split_by_ai"], stats["retried"], stats["split_without_ai"]) == (0, 0, 0)
        assert len(store.chunker.calls) == 6

    def test_a_broken_chunker_fails_the_whole_add_loudly(self, store):
        docs, metas, ids = _pages(6)
        store.chunker = FakeChunker(boom=docs[2])
        with pytest.raises(RuntimeError, match="chunker broke"):
            store.add_documents(docs, metas, ids)
        assert store.collection.added is None                 # nothing half-written

    def test_the_engine_reports_that_failure_to_its_caller(self, store):
        eng = engine_mod.LLMDocumentVectorEngine.__new__(engine_mod.LLMDocumentVectorEngine)
        eng.vector_store, eng.is_initialized = store, True
        import logging
        eng.logger = logging.getLogger("test-engine")
        docs, metas, ids = _pages(4)
        store.chunker = FakeChunker(boom=docs[1])
        batch = [{"text": t, "metadata": m, "id": i} for t, m, i in zip(docs, metas, ids)]
        assert eng.add_documents(batch) is False
        store.chunker = FakeChunker()
        assert eng.add_documents(batch) is True
        assert eng.last_chunking()["documents"] == 4


class TestWhatTheCallerIsTold:

    def test_each_request_thread_sees_its_own_report(self, store):
        seen = {}

        def add(n, name):
            docs, metas, ids = _pages(n)
            store.add_documents(docs, metas, [f"{name}_{i}" for i in ids])
            time.sleep(0.05)
            seen[name] = store.last_chunking()["documents"]

        threads = [threading.Thread(target=add, args=(5, "a")), threading.Thread(target=add, args=(9, "b"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert seen == {"a": 5, "b": 9}
        assert store.last_chunking() is None                  # this thread added nothing

    def test_with_chunking_switched_off_there_is_no_report(self, store, monkeypatch):
        monkeypatch.setattr(cfg, "VECTOR_ENABLE_CHUNKING", False, raising=False)
        docs, metas, ids = _pages(3)
        store.add_documents(docs, metas, ids)
        assert store.last_chunking() is None
        assert store.collection.added["ids"] == ids           # whole pages, as before
        assert store.chunker.calls == []

    def test_the_service_passes_the_report_on(self, monkeypatch):
        api = pytest.importorskip("app_vector_api")

        class FakeEngine:
            def add_documents(self, data):
                return True

            def last_chunking(self):
                return {"documents": len(calls["data"]), "parallel": 8, "seconds": 1.5, "split_by_ai": 2,
                        "retried": 1, "split_without_ai": 1, "split_without_ai_ids": ["doc_p2"]}

        calls = {}

        def fake_engine():
            return FakeEngine()
        monkeypatch.setattr(api, "get_vector_engine", fake_engine)
        client = api.app.test_client()
        payload = [{"id": f"doc_p{i}", "text": "t", "metadata": {}} for i in (1, 2)]
        calls["data"] = payload
        resp = client.post("/documents/batch", json=payload)
        body = resp.get_json()
        assert resp.status_code == 200 and body["status"] == "success"
        assert body["message"] == "Added 2 documents successfully"       # what callers already read
        assert body["chunking"]["split_without_ai_ids"] == ["doc_p2"]
