"""Deterministic chart rendering for the agentic engine (plan §4, P4).

Chart-rendering logic copied and adapted from LLMAnalyticalEngine._render_chart_from_spec
(the proven spec-based renderer V2 built to bypass PandasAI). Differences:
  * renders to a unique temp file, converts to a base64 data URI, then deletes
    the file — no shared temp_chart.png to clobber under concurrency, no disk leak;
  * self-contained (no engine/self state); the model supplies the spec.

Output contract matches V2 chart answers: an <img src="data:image/png;base64,...">
string the routes already know how to render.
"""
import base64
import logging
import os
import uuid

logger = logging.getLogger("nlq_agentic.charts")

SUPPORTED_CHART_TYPES = ("bar", "stacked_bar", "line", "pie", "scatter", "histogram", "area")


def render_chart(df, chart_type="bar", x_column=None, y_column=None, title=""):
    """Render a chart from a DataFrame + spec. Returns (base64_png, error).

    On success: (str, None). On failure: (None, reason). Never raises.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    chart_path = None
    try:
        if df is None or len(df) == 0:
            return None, "the dataset is empty"

        x_col, y_col = x_column, y_column
        if x_col and x_col not in df.columns:
            return None, f"x_column '{x_col}' is not a column in the dataset"
        if y_col and y_col not in df.columns:
            return None, f"y_column '{y_col}' is not a column in the dataset"

        df_clean = df.dropna(subset=[y_col]) if y_col else df.dropna()
        if len(df_clean) == 0:
            return None, "all rows are empty after dropping missing values"

        labels = df_clean[x_col].tolist() if x_col else df_clean.index.tolist()

        if y_col:
            values = df_clean[y_col].tolist()
        else:
            numeric_cols = df_clean.select_dtypes(include=["number"]).columns.tolist()
            if not numeric_cols:
                return None, "no numeric column to plot"
            y_col = numeric_cols[-1]
            values = df_clean[y_col].tolist()

        fig, ax = plt.subplots(figsize=(10, 6))
        ylabel = y_col.replace("_", " ").title() if y_col else ""

        if chart_type == "pie":
            filtered = [(l, v) for l, v in zip(labels, values) if v and v > 0]
            if not filtered:
                plt.close("all")
                return None, "a pie chart needs positive values"
            pie_labels, pie_values = zip(*filtered)
            ax.pie(pie_values, labels=pie_labels, autopct="%1.1f%%", startangle=90)
            ax.axis("equal")
        elif chart_type == "line":
            ax.plot(range(len(labels)), values, marker="o", linewidth=2, markersize=6)
            ax.set_xticks(range(len(labels)))
            ax.set_xticklabels(labels, rotation=45, ha="right")
            ax.set_ylabel(ylabel)
            ax.grid(True, alpha=0.3)
        elif chart_type == "scatter":
            numeric_cols = df_clean.select_dtypes(include=["number"]).columns.tolist()
            if len(numeric_cols) >= 2:
                ax.scatter(df_clean[numeric_cols[0]], df_clean[numeric_cols[1]], alpha=0.6)
                ax.set_xlabel(numeric_cols[0].replace("_", " ").title())
                ax.set_ylabel(numeric_cols[1].replace("_", " ").title())
            else:
                _bar(ax, labels, values, ylabel)
        elif chart_type == "histogram":
            ax.hist(values, bins="auto", color="steelblue", edgecolor="white")
            ax.set_xlabel(ylabel)
            ax.set_ylabel("Frequency")
        elif chart_type == "area":
            ax.fill_between(range(len(labels)), values, alpha=0.4, color="steelblue")
            ax.plot(range(len(labels)), values, linewidth=2, color="steelblue")
            ax.set_xticks(range(len(labels)))
            ax.set_xticklabels(labels, rotation=45, ha="right")
            ax.set_ylabel(ylabel)
        else:  # bar, stacked_bar, or unknown -> bar
            _bar(ax, labels, values, ylabel)

        if title:
            ax.set_title(title)
        elif y_col and x_col:
            ax.set_title(f"{ylabel} by {x_col.replace('_', ' ').title()}")

        plt.tight_layout()

        chart_dir = os.path.join(
            os.getenv("APP_ROOT", os.path.dirname(os.path.abspath(__file__))), "exports", "charts"
        )
        os.makedirs(chart_dir, exist_ok=True)
        chart_path = os.path.join(chart_dir, f"agentic_chart_{uuid.uuid4().hex}.png")
        plt.savefig(chart_path, dpi=100, bbox_inches="tight")
        plt.close("all")

        with open(chart_path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()
        return b64, None

    except Exception as e:
        logger.error(f"[charts] render failed: {e}")
        try:
            plt.close("all")
        except Exception:
            pass
        return None, f"chart rendering error: {e}"
    finally:
        # No disk leak — the image now lives in the base64 data URI.
        if chart_path and os.path.exists(chart_path):
            try:
                os.remove(chart_path)
            except Exception:
                pass


def _bar(ax, labels, values, ylabel):
    ax.bar(range(len(labels)), values, color="steelblue")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_ylabel(ylabel)


def img_html(b64_png):
    """Wrap a base64 PNG in the <img> data-URI the routes expect in special_message."""
    return f'<img src="data:image/png;base64,{b64_png}"/>'
