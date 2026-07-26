"""Tests for the knowledge brute-force routing gate (pages AND char budget — PB fix).

The page threshold alone was a bad proxy for context size (measured: 51 pages =
1.15M chars sent uncapped per question). These tests lock the dual-gate decision.
"""
import pytest

import agent_knowledge_integration as aki


@pytest.fixture
def gate_cfg(monkeypatch):
    """Pin the two knobs to known values for deterministic decisions."""
    monkeypatch.setattr(aki.cfg, 'KNOWLEDGE_BRUTE_FORCE_PAGE_THRESHOLD', 500, raising=False)
    monkeypatch.setattr(aki.cfg, 'KNOWLEDGE_BRUTE_FORCE_CHAR_BUDGET', 400_000, raising=False)
    return aki.cfg


@pytest.mark.unit
class TestBruteForceWithinBudget:
    def test_small_agent_stays_brute_force(self, gate_cfg):
        assert aki._brute_force_within_budget(50, 120_000) is True

    def test_page_threshold_still_gates(self, gate_cfg):
        assert aki._brute_force_within_budget(501, 100_000) is False

    def test_char_budget_gates_under_page_threshold(self, gate_cfg):
        # The measured live case: 396 pages (under 500) but 1.3M chars
        assert aki._brute_force_within_budget(396, 1_304_486) is False

    def test_51_page_288k_token_agent_now_routes_to_retrieval(self, gate_cfg):
        assert aki._brute_force_within_budget(51, 1_150_393) is False

    def test_exact_boundaries_inclusive(self, gate_cfg):
        assert aki._brute_force_within_budget(500, 400_000) is True

    def test_budget_disabled_restores_page_only_gating(self, gate_cfg, monkeypatch):
        monkeypatch.setattr(aki.cfg, 'KNOWLEDGE_BRUTE_FORCE_CHAR_BUDGET', 0, raising=False)
        assert aki._brute_force_within_budget(396, 1_304_486) is True
        assert aki._brute_force_within_budget(501, 10) is False

    def test_empty_knowledge_is_brute_force(self, gate_cfg):
        assert aki._brute_force_within_budget(0, 0) is True
