"""
Command Center — PDF Renderer
================================
Generates PDF files from content blocks.
"""

import io
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def render_blocks_to_pdf(blocks: List[Dict[str, Any]], title: str = "Report") -> Optional[bytes]:
    """
    Render content blocks to a PDF file.
    Returns PDF bytes or None if reportlab is not available.
    """
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib import colors
    except ImportError:
        logger.warning("reportlab not installed, PDF generation unavailable")
        return None

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = []

    # Title
    elements.append(Paragraph(title, styles['Title']))
    elements.append(Spacer(1, 12))

    for block in blocks:
        block_type = block.get("type", "text")

        if block_type == "text":
            content = block.get("content", "")
            # Simple markdown to text conversion
            content = content.replace("## ", "").replace("**", "").replace("*", "")
            elements.append(Paragraph(content, styles['Normal']))
            elements.append(Spacer(1, 6))

        elif block_type == "table":
            headers = block.get("headers", [])
            rows = block.get("rows", [])
            if headers and rows:
                table_data = [headers] + rows
                t = Table(table_data)
                t.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('GRID', (0, 0), (-1, -1), 1, colors.black),
                    ('FONTSIZE', (0, 0), (-1, -1), 8),
                ]))
                if block.get("title"):
                    elements.append(Paragraph(block["title"], styles['Heading2']))
                elements.append(t)
                elements.append(Spacer(1, 12))

        elif block_type == "kpi":
            for card in block.get("cards", []):
                kpi_text = f"{card.get('label', '')}: {card.get('value', '')}"
                if card.get("trend"):
                    kpi_text += f" ({card['trend']})"
                elements.append(Paragraph(kpi_text, styles['Heading3']))

    doc.build(elements)
    return buffer.getvalue()
