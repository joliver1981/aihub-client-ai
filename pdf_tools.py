"""
PDF manipulation utilities.

Pure functions: take PDF bytes in, return a list of (filename, bytes) tuples
out. No I/O — callers persist the bytes wherever they want (CC's
ArtifactManager, GeneralAgent's session files dir, a local path, etc.).

Shared by command_center_service (manipulate_pdf tool) and GeneralAgent
(same tool, exposed via core_tools.yaml).
"""

from __future__ import annotations

import io
from typing import Iterable, List, Set, Tuple

from pypdf import PdfReader, PdfWriter

PdfOutput = Tuple[str, bytes]


def _parse_page_spec(spec: str, page_count: int) -> List[int]:
    """
    Parse a 1-indexed page spec like "1,3,5-7,10" or "all" into a sorted list
    of 1-indexed page numbers. Raises ValueError on bad input.
    """
    if spec is None or not str(spec).strip() or str(spec).strip().lower() == "all":
        return list(range(1, page_count + 1))

    pages: Set[int] = set()
    for token in str(spec).split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            lo_s, hi_s = token.split("-", 1)
            lo, hi = int(lo_s), int(hi_s)
            if lo > hi:
                lo, hi = hi, lo
            for p in range(lo, hi + 1):
                pages.add(p)
        else:
            pages.add(int(token))

    bad = [p for p in pages if p < 1 or p > page_count]
    if bad:
        raise ValueError(
            f"Page(s) {sorted(bad)} out of range — PDF has {page_count} page(s)."
        )
    return sorted(pages)


def _writer_to_bytes(writer: PdfWriter) -> bytes:
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _read(pdf_bytes: bytes) -> PdfReader:
    return PdfReader(io.BytesIO(pdf_bytes))


def split_all(pdf_bytes: bytes, name_stem: str) -> List[PdfOutput]:
    """One single-page PDF per page in the input."""
    reader = _read(pdf_bytes)
    outputs: List[PdfOutput] = []
    width = max(2, len(str(len(reader.pages))))
    for i, page in enumerate(reader.pages, start=1):
        writer = PdfWriter()
        writer.add_page(page)
        outputs.append((f"{name_stem}_page_{i:0{width}d}.pdf", _writer_to_bytes(writer)))
    return outputs


def extract_pages(pdf_bytes: bytes, name_stem: str, pages_spec: str) -> List[PdfOutput]:
    """Extract the specified pages into a single new PDF."""
    reader = _read(pdf_bytes)
    page_nums = _parse_page_spec(pages_spec, len(reader.pages))
    if not page_nums:
        raise ValueError("No pages selected.")
    writer = PdfWriter()
    for p in page_nums:
        writer.add_page(reader.pages[p - 1])
    label = _label_for_pages(page_nums)
    return [(f"{name_stem}_pages_{label}.pdf", _writer_to_bytes(writer))]


def merge(pdfs: Iterable[Tuple[str, bytes]], output_name: str) -> List[PdfOutput]:
    """
    Concatenate multiple PDFs in order. `pdfs` is an iterable of
    (source_filename, bytes) — source filename is only used for the error
    message if reading fails.
    """
    writer = PdfWriter()
    any_pages = False
    for src_name, b in pdfs:
        try:
            reader = _read(b)
        except Exception as e:
            raise ValueError(f"Could not read {src_name!r}: {e}") from e
        for page in reader.pages:
            writer.add_page(page)
            any_pages = True
    if not any_pages:
        raise ValueError("No pages to merge.")
    if not output_name.lower().endswith(".pdf"):
        output_name += ".pdf"
    return [(output_name, _writer_to_bytes(writer))]


def rotate(
    pdf_bytes: bytes, name_stem: str, degrees: int, pages_spec: str = "all"
) -> List[PdfOutput]:
    """Rotate specified pages by 90/180/270 degrees clockwise."""
    if degrees not in (90, 180, 270):
        raise ValueError(f"degrees must be 90, 180, or 270 (got {degrees}).")
    reader = _read(pdf_bytes)
    targets = set(_parse_page_spec(pages_spec, len(reader.pages)))
    writer = PdfWriter()
    for i, page in enumerate(reader.pages, start=1):
        if i in targets:
            page.rotate(degrees)
        writer.add_page(page)
    return [(f"{name_stem}_rotated.pdf", _writer_to_bytes(writer))]


def page_count(pdf_bytes: bytes) -> int:
    return len(_read(pdf_bytes).pages)


def _label_for_pages(page_nums: List[int]) -> str:
    """Compact label for filenames: contiguous runs become ranges."""
    if not page_nums:
        return "none"
    runs: List[str] = []
    start = prev = page_nums[0]
    for n in page_nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        runs.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    runs.append(str(start) if start == prev else f"{start}-{prev}")
    label = "_".join(runs)
    return label if len(label) <= 40 else f"{page_nums[0]}-{page_nums[-1]}_etc"
