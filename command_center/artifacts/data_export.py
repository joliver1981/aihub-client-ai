"""Persist large tabular results as full-fidelity CSV artifacts.

The by-reference half of the artifact-sharing plan (docs/agent-artifact-
sharing-plan.md Phase 1): when a data agent's query result exceeds the inline
threshold, the FULL result is written to the shared artifact store as CSV and
callers ship a lightweight handle (artifact content block) instead of the
rows. CSV-first by design — the store holds raw bytes and the CC service
serves them verbatim (FileResponse), so no parquet/pyarrow dependency exists
anywhere on this path.

Dependency-light on purpose (pandas + the artifacts package) so both the main
app (aihub2.1) and unit tests can use it without service stacks.
"""
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Sidecar sanity: the source SQL is provenance, not a data channel.
_MAX_SOURCE_CHARS = 4000
_MAX_NAME_CHARS = 40


def _slugify(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", str(text or ""))[:_MAX_NAME_CHARS].strip("_")
    return slug or "query_result"


def persist_dataframe_artifact(
    df,
    session_scope: str,
    name_hint: str = "",
    producing_agent: Optional[str] = None,
    source: Optional[str] = None,
    manager=None,
) -> Optional[dict]:
    """Write one DataFrame to the shared store as a CSV artifact.

    Returns the artifact CONTENT BLOCK (dict, ready for UI/delegation payloads)
    or None on failure. Never raises — persisting the artifact must not be able
    to break the answer that produced it.

    Args:
        df: pandas DataFrame (full fidelity — caller decides thresholds)
        session_scope: store scope, "{user_id}/{session_id}" or bare session id
        name_hint: human text (e.g. the question) used for the file name
        producing_agent: provenance, e.g. "data_agent:281"
        source: provenance, e.g. the SQL query
        manager: ArtifactManager override (tests); defaults to the shared store
    """
    try:
        from command_center.artifacts.artifact_manager import get_shared_artifact_manager
        from command_center.artifacts.artifact_models import ArtifactType

        mgr = manager if manager is not None else get_shared_artifact_manager()

        # utf-8-sig so Excel opens unicode CSVs correctly on Windows.
        csv_bytes = df.to_csv(index=False).encode("utf-8-sig")

        meta = mgr.create(
            f"{_slugify(name_hint)}_{len(df)}_rows",
            ArtifactType.CSV,
            csv_bytes,
            session_scope,
            producing_agent=producing_agent,
            source=(str(source)[:_MAX_SOURCE_CHARS] if source else None),
            row_count=int(len(df)),
            columns=[str(c) for c in df.columns],
        )
        block = meta.to_content_block()
        block["description"] = (
            f"Full result — {len(df):,} rows × {len(df.columns)} columns (CSV)"
        )
        logger.info(f"[data_export] Persisted {len(df)} rows as artifact "
                    f"{meta.artifact_id} ({meta.size_display}) scope={session_scope}")
        return block
    except Exception as e:
        logger.error(f"[data_export] Artifact persist failed (answer unaffected): {e}",
                     exc_info=True)
        return None


def maybe_persist_result_artifacts(
    answer,
    answer_type: str,
    session_scope: str,
    name_hint: str = "",
    producing_agent: Optional[str] = None,
    source: Optional[str] = None,
    threshold: Optional[int] = None,
    manager=None,
) -> list:
    """Persist a data-agent answer's DataFrame(s) when they exceed `threshold`.

    Handles answer_type "dataframe" (single df) and "multi_dataframe" (list).
    Returns a list of artifact content blocks ([] when nothing qualified or on
    any failure). threshold=None reads config.ARTIFACT_EXPORT_ROW_THRESHOLD;
    threshold<=0 disables persisting entirely.
    """
    try:
        import pandas as pd

        if threshold is None:
            try:
                import config as _cfg
                threshold = int(getattr(_cfg, "ARTIFACT_EXPORT_ROW_THRESHOLD", 10000))
            except Exception:
                threshold = 10000
        if threshold <= 0:
            return []

        if answer_type == "dataframe" and isinstance(answer, pd.DataFrame):
            dfs = [answer]
        elif answer_type == "multi_dataframe" and isinstance(answer, (list, tuple)):
            dfs = [d for d in answer if isinstance(d, pd.DataFrame)]
        elif answer_type in ("dataframe", "multi_dataframe"):
            # AIHUB-0023: a dataframe-typed answer that is NOT a DataFrame is a
            # shape anomaly worth surfacing — a silent [] here cost a full e2e
            # round of diagnosis.
            logger.warning(
                f"[data_export] answer_type={answer_type} but answer is "
                f"{type(answer).__name__} — no artifact (shape anomaly)")
            return []
        else:
            return []

        blocks = []
        for i, df in enumerate(dfs):
            if len(df) > threshold:
                hint = name_hint if len(dfs) == 1 else f"{name_hint}_{i + 1}"
                block = persist_dataframe_artifact(
                    df, session_scope, name_hint=hint,
                    producing_agent=producing_agent, source=source, manager=manager,
                )
                if block:
                    blocks.append(block)
            else:
                # AIHUB-0023: the skip must be observable — it doubles as the
                # one-probe check that the RUNNING process actually loaded the
                # intended threshold (the e2e round failed on an unloaded .env
                # edit with nothing in the logs to say so).
                logger.info(
                    f"[data_export] dataframe below export threshold "
                    f"(rows={len(df)} <= threshold={threshold}) — no artifact")
        return blocks
    except Exception as e:
        logger.error(f"[data_export] maybe_persist failed (answer unaffected): {e}")
        return []
