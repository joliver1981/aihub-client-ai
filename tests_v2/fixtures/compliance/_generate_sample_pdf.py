"""Generate sample_compliance.pdf — minimal valid PDF for compliance tests.

We write a hand-crafted single-page PDF without any third-party deps so the
tests don't require reportlab/fpdf to be installed in the test environment.
The PDF contains a short known string so a real extractor could read it,
but the compliance tests all mock the LLM call, so byte-correctness and
hashability are what matter.
"""
from pathlib import Path


def build_minimal_pdf(text: str = "AI Hub Compliance Test Document. Pallet height: 84 in.") -> bytes:
    # Build objects
    content_stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = []
    # 1: catalog
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    # 2: pages
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    # 3: page
    objects.append(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
    )
    # 4: content stream
    stream_obj = (
        b"<< /Length " + str(len(content_stream)).encode("ascii") + b" >>\n"
        b"stream\n" + content_stream + b"\nendstream"
    )
    objects.append(stream_obj)
    # 5: font
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    # Assemble
    out = bytearray()
    out += b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("ascii")
        out += obj
        out += b"\nendobj\n"

    xref_pos = len(out)
    out += f"xref\n0 {len(objects)+1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


if __name__ == "__main__":
    pdf_bytes = build_minimal_pdf()
    out_path = Path(__file__).parent / "sample_compliance.pdf"
    out_path.write_bytes(pdf_bytes)
    print(f"Wrote {out_path} ({len(pdf_bytes)} bytes)")
