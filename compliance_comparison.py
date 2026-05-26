import json
import logging
import os
from typing import Dict, List, Optional
from datetime import datetime

from CommonUtils import get_db_connection
from compliance_engine import ComplianceEngine

logger = logging.getLogger("ComplianceComparison")


class ComplianceComparison:
    """Compares compliance requirements across versions or retailers."""

    def compare_versions(
        self, version_a_id: int, version_b_id: int
    ) -> Optional[Dict]:
        """Compare two versions of the same retailer's document set."""
        reqs_a = ComplianceEngine.get_requirements(version_a_id)
        reqs_b = ComplianceEngine.get_requirements(version_b_id)

        if not reqs_a and not reqs_b:
            return None

        matched = self._match_requirements(reqs_a, reqs_b)
        details = self._evaluate_matches(matched)
        summary = self._build_summary(details)

        return {"summary": summary, "details": details}

    def compare_retailers(
        self,
        retailer_a_id: int,
        retailer_b_id: int,
        category_filter: str = None,
    ) -> Optional[Dict]:
        """Compare current requirements across two retailers."""
        reqs_a = ComplianceEngine.get_current_requirements_for_retailer(
            retailer_a_id, category_filter
        )
        reqs_b = ComplianceEngine.get_current_requirements_for_retailer(
            retailer_b_id, category_filter
        )

        if not reqs_a and not reqs_b:
            return None

        matched = self._match_requirements(reqs_a, reqs_b)
        details = self._evaluate_matches(matched)
        summary = self._build_summary(details)

        return {"summary": summary, "details": details}

    def store_comparison(
        self,
        comparison_type: str,
        source_a_id: int,
        source_b_id: int,
        result: Dict,
        created_by: int = 1,
    ) -> int:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                """
                INSERT INTO ComparisonResults
                    (comparison_type, source_a_id, source_b_id, result_json, created_by)
                OUTPUT INSERTED.comparison_id
                VALUES (?, ?, ?, ?, ?)
                """,
                comparison_type,
                source_a_id,
                source_b_id,
                json.dumps(result, default=str),
                created_by,
            )
            comparison_id = cursor.fetchone()[0]
            conn.commit()
            return comparison_id
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_comparison(comparison_id: int) -> Optional[Dict]:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            cursor.execute(
                """
                SELECT comparison_id, comparison_type, source_a_id, source_b_id,
                       result_json, created_by, created_at
                FROM ComparisonResults
                WHERE comparison_id = ?
                """,
                comparison_id,
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "comparison_id": row.comparison_id,
                "comparison_type": row.comparison_type,
                "source_a_id": row.source_a_id,
                "source_b_id": row.source_b_id,
                "result": json.loads(row.result_json) if row.result_json else None,
                "created_by": row.created_by,
                "created_at": row.created_at,
            }
        finally:
            cursor.close()
            conn.close()

    # -- matching logic ------------------------------------------------------

    def _match_requirements(
        self, reqs_a: List[Dict], reqs_b: List[Dict]
    ) -> List[Dict]:
        """Match requirements by category + subcategory, producing a diff list."""
        index_b = {}
        for req in reqs_b:
            key = (req["category"], req["subcategory"])
            index_b.setdefault(key, []).append(req)

        matched = []
        seen_b_keys = set()

        for req_a in reqs_a:
            key = (req_a["category"], req_a["subcategory"])
            candidates = index_b.get(key, [])

            if candidates:
                best = self._find_best_match(req_a, candidates)
                if best:
                    matched.append(
                        {
                            "category": req_a["category"],
                            "subcategory": req_a["subcategory"],
                            "value_a": req_a.get("requirement_text", ""),
                            "specific_value_a": req_a.get("specific_value"),
                            "severity_a": req_a.get("severity"),
                            "value_b": best.get("requirement_text", ""),
                            "specific_value_b": best.get("specific_value"),
                            "severity_b": best.get("severity"),
                            "change_type": "modified",
                        }
                    )
                    seen_b_keys.add(id(best))
                    continue

            matched.append(
                {
                    "category": req_a["category"],
                    "subcategory": req_a["subcategory"],
                    "value_a": req_a.get("requirement_text", ""),
                    "specific_value_a": req_a.get("specific_value"),
                    "severity_a": req_a.get("severity"),
                    "value_b": None,
                    "specific_value_b": None,
                    "severity_b": None,
                    "change_type": "removed",
                }
            )

        for req_b in reqs_b:
            if id(req_b) not in seen_b_keys:
                matched.append(
                    {
                        "category": req_b["category"],
                        "subcategory": req_b["subcategory"],
                        "value_a": None,
                        "specific_value_a": None,
                        "severity_a": None,
                        "value_b": req_b.get("requirement_text", ""),
                        "specific_value_b": req_b.get("specific_value"),
                        "severity_b": req_b.get("severity"),
                        "change_type": "added",
                    }
                )

        return matched

    def _find_best_match(self, req_a: Dict, candidates: List[Dict]) -> Optional[Dict]:
        """Find the best matching requirement from candidates by text similarity."""
        text_a = (req_a.get("requirement_text", "") or "").lower()
        best_score = 0
        best_match = None
        for candidate in candidates:
            text_b = (candidate.get("requirement_text", "") or "").lower()
            words_a = set(text_a.split())
            words_b = set(text_b.split())
            if not words_a or not words_b:
                continue
            overlap = len(words_a & words_b) / max(len(words_a | words_b), 1)
            if overlap > best_score:
                best_score = overlap
                best_match = candidate
        return best_match if best_score > 0.15 else None

    def _evaluate_matches(self, matched: List[Dict]) -> List[Dict]:
        """Determine if each matched pair represents a meaningful change."""
        try:
            from smart_change_detector import SmartChangeDetector, ChangeCandidate

            candidates = []
            for m in matched:
                if m["change_type"] == "modified":
                    candidates.append(
                        ChangeCandidate(
                            row_key=f"{m['category']}.{m['subcategory']}",
                            row_context={
                                "category": m["category"],
                                "subcategory": m["subcategory"],
                            },
                            field="requirement",
                            old_value=m["value_a"],
                            new_value=m["value_b"],
                        )
                    )

            evaluations = {}
            if candidates:
                detector = SmartChangeDetector()
                evaluations = detector.evaluate_changes(
                    changes=candidates, strictness="strict"
                )

            results = []
            for m in matched:
                key = f"{m['category']}.{m['subcategory']}"
                evaluation = evaluations.get(key)

                if m["change_type"] in ("added", "removed"):
                    is_meaningful = True
                    reason = f"Requirement {m['change_type']}"
                elif evaluation:
                    is_meaningful = evaluation.should_update
                    reason = evaluation.reason
                else:
                    is_meaningful = m["value_a"] != m["value_b"]
                    reason = "Text differs" if is_meaningful else "Identical"

                results.append(
                    {
                        "category": m["category"],
                        "subcategory": m["subcategory"],
                        "value_a": m["value_a"],
                        "value_b": m["value_b"],
                        "specific_value_a": m["specific_value_a"],
                        "specific_value_b": m["specific_value_b"],
                        "severity_a": m["severity_a"],
                        "severity_b": m["severity_b"],
                        "change_type": m["change_type"],
                        "is_meaningful": is_meaningful,
                        "reason": reason,
                    }
                )

            return results

        except ImportError:
            logger.warning(
                "SmartChangeDetector not available — falling back to text comparison"
            )
            results = []
            for m in matched:
                is_meaningful = m["change_type"] != "modified" or m["value_a"] != m["value_b"]
                results.append(
                    {
                        **m,
                        "is_meaningful": is_meaningful,
                        "reason": m["change_type"]
                        if m["change_type"] != "modified"
                        else ("Text differs" if is_meaningful else "Identical"),
                    }
                )
            return results

    def _build_summary(self, details: List[Dict]) -> List[Dict]:
        """Aggregate detail rows into per-category summaries."""
        categories = {}
        for d in details:
            cat = d["category"]
            if cat not in categories:
                categories[cat] = {
                    "category": cat,
                    "total": 0,
                    "meaningful_changes": 0,
                    "cosmetic_changes": 0,
                    "added": 0,
                    "removed": 0,
                }
            s = categories[cat]
            s["total"] += 1
            if d["change_type"] == "added":
                s["added"] += 1
                s["meaningful_changes"] += 1
            elif d["change_type"] == "removed":
                s["removed"] += 1
                s["meaningful_changes"] += 1
            elif d.get("is_meaningful"):
                s["meaningful_changes"] += 1
            else:
                s["cosmetic_changes"] += 1

        return sorted(categories.values(), key=lambda x: x["category"])
