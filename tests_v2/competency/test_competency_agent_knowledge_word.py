"""LEVEL 3 — Word agent-knowledge COMPETENCY suite.

For each .docx fixture in tests_v2/fixtures/docs/competency_word/, this
suite uploads it to a fresh agent, asks fingerprinted questions, and
scores them by capability dimension:

    direct_lookup            single fact in body text
    heading_nav              "what does section X cover?"
    bullet_extract           fact in a bullet list
    table_in_word            fact inside a docx table
    chart_caption            fact stated in a chart's figure caption
    chart_data_point         fact in the chart but only in the IMAGE
                             (extractor sees caption + body, not pixels)
    tracked_change_accepted  the post-revision (current) value, post
                             "accept all changes"
    tracked_change_rejected  must NOT echo the deleted/previous value
                             from a tracked deletion (we treat that as
                             a leak — same machinery as hidden_security
                             in the Excel suite)
    footnote_extract         fact stated in a footnote
    long_doc_retrieval       fact buried at the back of a 30-page doc
    not_present              correct answer is "no, not present"

See `tests_v2/competency/_runner.py` for the lifecycle (provision agent →
upload → ask → score → tear down).

Report (regenerated each run):
    tests_v2/artifacts/competency/word_competency_report.md
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from .conftest import (
    MAIN_BASE,
    API_KEY,
    ARTIFACT_PREFIX,
    INDEX_WAIT_SECONDS,
)
from ._runner import run_competency


FIXTURES_DIR = (
    Path(__file__).parent.parent / "fixtures" / "docs" / "competency_word"
)
SCORE_FLOOR = float(os.getenv("COMPETENCY_SCORE_FLOOR_WORD", "0.50"))


# =============================================================================
# Question battery
# =============================================================================

# Deletion/prior qualifiers that make mentioning a tracked-DELETED figure
# CORRECT rather than a leak ("the prior $5,000 was removed by tracked
# deletion"). Used by the tracked_change_accepted negative pattern below;
# matched case-insensitively by the runner.
_DELETED_QUALIFIER = (
    r"(?:delet|remov|struck|stricken|strike|supersed|redlin|revis|track"
    r"|\bprior\b|previous|earlier|\bformer\b|original|\bold\b|\bdraft\b"
    r"|replac|reject|withdraw|discard|cross(?:ed)?[- ]out|no\s+longer)"
)

QUESTIONS = [

    # =========================================================================
    # 01 — Clean handbook
    # =========================================================================
    (
        "01_clean_handbook.docx",
        "Who founded Veridian Labs and when?",
        [r"Anika\s+Vorhees", r"Marcus\s+Holloway", r"March\s+2014",
         r"founded\s+in\s+(?:March\s+)?2014"],
        ["direct_lookup"],
        None, 1.0,
    ),
    (
        "01_clean_handbook.docx",
        "How many manufacturing or operating sites does Veridian Labs have?",
        [r"\b5\b\s*sites?", r"\bfive\b\s*sites?"],
        ["direct_lookup", "bullet_extract"],
        None, 1.0,
    ),
    (
        "01_clean_handbook.docx",
        "How many vacation days does an employee with 7 years of service receive at Veridian Labs?",
        # 6-10 yrs → 25 days
        [r"\b25\b\s*(?:days|vacation)?", r"twenty[- ]?five"],
        ["bullet_extract", "direct_lookup"],
        None, 1.0,
    ),
    (
        "01_clean_handbook.docx",
        "What is the daily per diem for travel to Tokyo?",
        # Tier 1 → $115/day
        [r"\$?115\b", r"\$115/day"],
        ["bullet_extract"],
        None, 1.0,
    ),
    (
        "01_clean_handbook.docx",
        "How often are performance reviews held at Veridian Labs?",
        [r"semi[- ]?annually", r"twice\s+a\s+year", r"every\s+six\s+months",
         r"April\s+and\s+October"],
        ["direct_lookup"],
        None, 1.0,
    ),
    (
        "01_clean_handbook.docx",
        "Does Veridian Labs have a code of conduct that protects whistleblowers from retaliation?",
        [r"\byes\b", r"retaliation", r"grounds\s+for\s+termination",
         r"protect"],
        ["direct_lookup"],
        None, 1.0,
    ),

    # =========================================================================
    # 02 — Tables-heavy report (Eldoria Logistics)
    # =========================================================================
    (
        "02_tables_heavy_report.docx",
        "Which Eldoria Logistics hub had the highest throughput in Q1 2026?",
        [r"\bMemphis\b"],
        ["table_in_word", "comparison"],
        None, 1.0,
    ),
    (
        "02_tables_heavy_report.docx",
        "What was Memphis's Q1 average daily parcel throughput?",
        [r"49[,.]?100\b", r"\b49100\b"],
        ["table_in_word", "direct_lookup"],
        None, 1.0,
    ),
    (
        "02_tables_heavy_report.docx",
        "Which service tier missed its on-time delivery SLA in Q1 2026?",
        [r"Priority\s+Overnight", r"priority\s+overnight"],
        ["table_in_word", "comparison"],
        None, 1.0,
    ),
    (
        "02_tables_heavy_report.docx",
        "What was the top revenue lane (origin → destination) for Eldoria Logistics in Q1?",
        [r"Memphis.*Atlanta", r"Atlanta.*Memphis"],
        ["table_in_word", "comparison"],
        None, 1.0,
    ),
    (
        "02_tables_heavy_report.docx",
        "What was Cologne's average damage claim amount in Q1 2026?",
        [r"\$?215\b", r"\$215"],
        ["table_in_word", "direct_lookup"],
        None, 1.0,
    ),

    # =========================================================================
    # 03 — Embedded charts + KPI commentary (Atlas Networks)
    # =========================================================================
    (
        "03_embedded_charts_kpis.docx",
        "How many active accounts did Atlas Networks have in March 2026?",
        [r"89[,.]?700", r"89\.7\s*K", r"89,700"],
        ["chart_caption", "direct_lookup"],
        None, 1.0,
    ),
    (
        "03_embedded_charts_kpis.docx",
        "What is Atlas Networks' FY2026 total ARR?",
        [r"\$?\s*248\s*M(?:illion)?", r"248\s*million"],
        ["direct_lookup"],
        None, 1.0,
    ),
    (
        "03_embedded_charts_kpis.docx",
        "What share of Atlas Networks' FY2026 ARR comes from the Atlas Core product line?",
        [r"\b42\s*%", r"42\s*percent"],
        ["chart_caption", "direct_lookup"],
        None, 1.0,
    ),
    (
        "03_embedded_charts_kpis.docx",
        "Who is Atlas Networks' CEO and CFO?",
        [r"Larissa\s+Verma.*Hideo\s+Tanaka|Hideo\s+Tanaka.*Larissa\s+Verma",
         r"Larissa\s+Verma", r"Hideo\s+Tanaka"],
        ["direct_lookup"],
        None, 1.0,
    ),
    (
        "03_embedded_charts_kpis.docx",
        "How many customers does Atlas Networks serve, and in how many countries?",
        [r"4[,.]?200.*23\s+countries", r"4,200.*23", r"4200.*23"],
        ["direct_lookup"],
        None, 1.0,
    ),

    # =========================================================================
    # 04 — Tracked changes contract
    # =========================================================================
    (
        "04_tracked_changes_contract.docx",
        "What is the monthly fee Cyrene Industries pays Westbrook & Vale under the MSA?",
        [r"\$?\s*48[,.]?500", r"\$48,500"],
        ["direct_lookup"],
        None, 1.0,
    ),
    (
        "04_tracked_changes_contract.docx",
        "What is the initial term of the Master Services Agreement?",
        # Match both "24 months", "twenty-four months", "twenty-four (24) months",
        # "two years", etc.
        [r"\b24\b", r"twenty[- ]?four", r"two\s+years?"],
        ["direct_lookup"],
        None, 1.0,
    ),
    (
        "04_tracked_changes_contract.docx",
        "What is the current penalty per business day for late delivery under Section 5?",
        # Post-tracked-change value = $12,500. The old $5,000 was DELETED.
        # If the agent surfaces $5,000 AS THE CURRENT VALUE it's reading
        # rejected revisions (a fidelity bug we want to catch).
        [r"\$?\s*12[,.]?500", r"\$12,500"],
        ["tracked_change_accepted"],
        # Leak = $5,000 presented WITHOUT a deletion/prior qualifier in the
        # same sentence/line. "the prior $5,000 was removed by tracked
        # deletion" is a CORRECT (better) answer, not a leak — the old
        # pattern's lookahead only forgave the literal phrase "USD per day
        # was removed" and false-positived both GA and The Agent on
        # 2026-08-27. The tempered scan below fires only when a sentence
        # (or table row/line) contains the figure and NO qualifier on
        # either side of it.
        [r"(?:^|[.!?\n])"
         r"(?:(?!" + _DELETED_QUALIFIER + r")[^.!?\n])*?"
         r"(?<!\d)5[,.]?000(?:\.\d+)?(?![,.]?\d)"
         r"(?:(?!" + _DELETED_QUALIFIER + r")[^.!?\n])*"
         r"(?:[.!?\n]|$)"],
        2.0,  # weighted higher — fidelity-critical
    ),
    (
        "04_tracked_changes_contract.docx",
        "How long does the confidentiality obligation survive after termination?",
        # Match "5 years", "five years", "five (5) years" — keep the
        # digit/word patterns independent so the parenthetical doesn't
        # break the match.
        [r"\b5\b", r"\bfive\b"],
        ["direct_lookup"],
        None, 1.0,
    ),

    # =========================================================================
    # 05 — Long doc with footnotes (Hyperion HLN reference)
    # =========================================================================
    (
        "05_long_doc_toc_footnotes.docx",
        "What is the peak event throughput of the Hyperion Logistics Network?",
        [r"28[,.]?000\s*events?/?s(?:ec)?", r"28k?\s+events?\s+per\s+second"],
        ["direct_lookup"],
        None, 1.0,
    ),
    (
        "05_long_doc_toc_footnotes.docx",
        "Which 3 cloud regions does Hyperion Logistics Network deploy to?",
        [r"us-east-1.*eu-central-1.*ap-southeast-2",
         r"us-east-1", r"eu-central-1", r"ap-southeast-2"],
        ["direct_lookup"],
        None, 1.0,
    ),
    (
        "05_long_doc_toc_footnotes.docx",
        "When was the last successful disaster-recovery drill performed?",
        [r"February\s+14,?\s+2026", r"Feb(?:ruary)?\s+14,?\s+2026",
         r"2026-02-14"],
        ["long_doc_retrieval", "direct_lookup"],
        None, 1.0,
    ),
    (
        "05_long_doc_toc_footnotes.docx",
        "Who authored RFC-HLN-031 and when was it approved?",
        # Mei-Ling Park, October 18 2025 (also Oct 25 — approval date)
        [r"Mei[- ]?Ling\s+Park", r"October\s+(?:18|25),?\s+2025"],
        ["footnote_extract", "long_doc_retrieval"],
        None, 1.0,
    ),
    (
        "05_long_doc_toc_footnotes.docx",
        "Does Hyperion Logistics Network use Azure as one of its cloud providers?",
        # It uses AWS and GCP, NOT Azure. Correct answer = no/not mentioned.
        [r"\bno\b", r"\bnot\b", r"only\s+AWS\s+and\s+GCP",
         r"do(?:es)?n[' ]?t\s+(?:use|mention)", r"AWS\s+and\s+GCP",
         r"not\s+(?:listed|mentioned|present|used)"],
        ["not_present"],
        None, 1.0,
    ),
    (
        "05_long_doc_toc_footnotes.docx",
        "What programming language is the router-core service written in, and what is its p95 latency?",
        [r"Rust.*84\s*ms|84\s*ms.*Rust"],
        ["direct_lookup"],
        None, 1.0,
    ),
]


# =============================================================================
# The test
# =============================================================================

@pytest.mark.competency
@pytest.mark.slow
def test_word_competency(http_session, reports_dir):
    run_competency(
        suite_name="word",
        fixtures_dir=FIXTURES_DIR,
        fixture_glob="*.docx",
        questions=QUESTIONS,
        http_session=http_session,
        main_base=MAIN_BASE,
        api_key=API_KEY,
        artifact_prefix=ARTIFACT_PREFIX,
        reports_dir=reports_dir,
        report_basename="word_competency_report",
        index_wait_seconds=INDEX_WAIT_SECONDS,
        score_floor=SCORE_FLOOR,
    )
