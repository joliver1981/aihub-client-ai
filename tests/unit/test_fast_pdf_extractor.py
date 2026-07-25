"""
Tests for fast_pdf_extractor.py
================================
Tests pure-logic functions: validate_extraction_quality, PDFType enum,
ExtractionMethod enum, PDFAnalysis dataclass, ExtractionResult dataclass,
FastPDFExtractor.__init__ defaults, detect_pdf_type (with mocked fitz),
extract_text_fast (with mocked fitz).
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch
from dataclasses import asdict

# ---------------------------------------------------------------------------
# Module-level mocks (before import)
# ---------------------------------------------------------------------------
_mock_common_utils = MagicMock()
_mock_common_utils.rotate_logs_on_startup = MagicMock()
_mock_common_utils.get_log_path = MagicMock(return_value="test_log.txt")

_saved = {}
for mod_name in ("CommonUtils",):
    _saved[mod_name] = sys.modules.get(mod_name)

sys.modules["CommonUtils"] = _mock_common_utils

if "fast_pdf_extractor" in sys.modules:
    del sys.modules["fast_pdf_extractor"]

with patch("logging.handlers.WatchedFileHandler", MagicMock()):
    from fast_pdf_extractor import (
        PDFType,
        ExtractionMethod,
        PDFAnalysis,
        ExtractionResult,
        validate_extraction_quality,
        detect_pdf_type,
        extract_text_fast,
        classify_page_needs_ai,
        FastPDFExtractor,
        MIN_CHARS_PER_PAGE,
        MIN_EXTRACTION_RATIO,
    )

for k, v in _saved.items():
    if v is not None:
        sys.modules[k] = v
    elif k in sys.modules:
        del sys.modules[k]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestPDFTypeEnum:
    """Test PDFType enum values."""

    def test_text(self):
        assert PDFType.TEXT.value == "text"

    def test_scanned(self):
        assert PDFType.SCANNED.value == "scanned"

    def test_mixed(self):
        assert PDFType.MIXED.value == "mixed"

    def test_empty(self):
        assert PDFType.EMPTY.value == "empty"

    def test_unknown(self):
        assert PDFType.UNKNOWN.value == "unknown"


@pytest.mark.unit
class TestExtractionMethodEnum:
    """Test ExtractionMethod enum values."""

    def test_pymupdf_direct(self):
        assert ExtractionMethod.PYMUPDF_DIRECT.value == "pymupdf_direct"

    def test_ai_vision(self):
        assert ExtractionMethod.AI_VISION.value == "ai_vision"

    def test_hybrid(self):
        assert ExtractionMethod.HYBRID.value == "hybrid"


@pytest.mark.unit
class TestPDFAnalysisDataclass:
    """Test PDFAnalysis dataclass."""

    def test_construction(self):
        analysis = PDFAnalysis(
            pdf_type=PDFType.TEXT,
            confidence=0.95,
            page_count=10,
            avg_chars_per_page=500.0,
            sampled_pages=5,
            scanned_pages_in_sample=0,
            has_fonts=True,
            has_images=False,
            recommendation="direct",
        )
        assert analysis.pdf_type == PDFType.TEXT
        assert analysis.confidence == 0.95
        assert analysis.page_count == 10
        assert analysis.error is None

    def test_with_error(self):
        analysis = PDFAnalysis(
            pdf_type=PDFType.UNKNOWN,
            confidence=0.0,
            page_count=0,
            avg_chars_per_page=0,
            sampled_pages=0,
            scanned_pages_in_sample=0,
            has_fonts=False,
            has_images=False,
            recommendation="ai",
            error="Test error",
        )
        assert analysis.error == "Test error"


@pytest.mark.unit
class TestExtractionResultDataclass:
    """Test ExtractionResult dataclass."""

    def test_construction(self):
        analysis = PDFAnalysis(
            pdf_type=PDFType.TEXT,
            confidence=1.0,
            page_count=5,
            avg_chars_per_page=300.0,
            sampled_pages=3,
            scanned_pages_in_sample=0,
            has_fonts=True,
            has_images=False,
            recommendation="direct",
        )
        result = ExtractionResult(
            pages=[{"page_number": 1, "text": "Hello"}],
            method_used=ExtractionMethod.PYMUPDF_DIRECT,
            pdf_analysis=analysis,
            fast_extraction_attempted=True,
            fast_extraction_success=True,
        )
        assert len(result.pages) == 1
        assert result.method_used == ExtractionMethod.PYMUPDF_DIRECT
        assert result.fallback_reason is None
        assert result.fast_page_count is None


@pytest.mark.unit
class TestConstants:
    """Test module-level constants."""

    def test_min_chars_per_page(self):
        assert MIN_CHARS_PER_PAGE == 100

    def test_min_extraction_ratio(self):
        assert MIN_EXTRACTION_RATIO == 0.7


@pytest.mark.unit
class TestValidateExtractionQuality:
    """Test validate_extraction_quality."""

    def test_no_pages(self):
        analysis = PDFAnalysis(
            pdf_type=PDFType.TEXT, confidence=1.0, page_count=5,
            avg_chars_per_page=500, sampled_pages=3,
            scanned_pages_in_sample=0, has_fonts=True,
            has_images=False, recommendation="direct",
        )
        is_valid, reason = validate_extraction_quality([], analysis)
        assert is_valid is False
        assert "No pages extracted" in reason

    def test_good_extraction(self):
        analysis = PDFAnalysis(
            pdf_type=PDFType.TEXT, confidence=1.0, page_count=2,
            avg_chars_per_page=500, sampled_pages=2,
            scanned_pages_in_sample=0, has_fonts=True,
            has_images=False, recommendation="direct",
        )
        pages = [
            {"page_number": 1, "text": "A" * 500},
            {"page_number": 2, "text": "B" * 500},
        ]
        is_valid, reason = validate_extraction_quality(pages, analysis)
        assert is_valid is True
        assert reason == ""

    def test_low_chars_with_expected_text(self):
        analysis = PDFAnalysis(
            pdf_type=PDFType.TEXT, confidence=1.0, page_count=2,
            avg_chars_per_page=500, sampled_pages=2,
            scanned_pages_in_sample=0, has_fonts=True,
            has_images=False, recommendation="direct",
        )
        pages = [
            {"page_number": 1, "text": "short"},
            {"page_number": 2, "text": "text"},
        ]
        is_valid, reason = validate_extraction_quality(pages, analysis)
        assert is_valid is False
        assert "Extracted avg" in reason

    def test_low_chars_with_images(self):
        analysis = PDFAnalysis(
            pdf_type=PDFType.MIXED, confidence=0.7, page_count=2,
            avg_chars_per_page=30, sampled_pages=2,
            scanned_pages_in_sample=1, has_fonts=True,
            has_images=True, recommendation="ai",
        )
        pages = [
            {"page_number": 1, "text": "x"},
            {"page_number": 2, "text": "y"},
        ]
        is_valid, reason = validate_extraction_quality(pages, analysis)
        assert is_valid is False
        assert "scanned" in reason

    def test_too_many_empty_pages(self):
        analysis = PDFAnalysis(
            pdf_type=PDFType.TEXT, confidence=1.0, page_count=4,
            avg_chars_per_page=100, sampled_pages=4,
            scanned_pages_in_sample=0, has_fonts=True,
            has_images=False, recommendation="direct",
        )
        # Avg chars = (500+0+0+0)/4 = 125 > min_chars_per_page, but 3/4 pages empty
        pages = [
            {"page_number": 1, "text": "A" * 500},
            {"page_number": 2, "text": ""},
            {"page_number": 3, "text": ""},
            {"page_number": 4, "text": ""},
        ]
        is_valid, reason = validate_extraction_quality(pages, analysis)
        assert is_valid is False
        assert "nearly empty" in reason

    def test_custom_min_chars(self):
        analysis = PDFAnalysis(
            pdf_type=PDFType.TEXT, confidence=1.0, page_count=1,
            avg_chars_per_page=50, sampled_pages=1,
            scanned_pages_in_sample=0, has_fonts=True,
            has_images=False, recommendation="direct",
        )
        pages = [{"page_number": 1, "text": "A" * 30}]
        # With min_chars_per_page=20, this should pass
        is_valid, _ = validate_extraction_quality(pages, analysis, min_chars_per_page=20)
        assert is_valid is True


@pytest.mark.unit
class TestDetectPdfType:
    """Test detect_pdf_type with mocked fitz."""

    @patch("fast_pdf_extractor._module_logger")
    def test_no_fitz_installed(self, mock_logger):
        with patch.dict(sys.modules, {"fitz": None}):
            import fast_pdf_extractor as fpe
            original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__
            def mock_import(name, *args, **kwargs):
                if name == "fitz":
                    raise ImportError("No module named 'fitz'")
                return original_import(name, *args, **kwargs)
            with patch("builtins.__import__", side_effect=mock_import):
                result = detect_pdf_type(b"fake pdf bytes")
            assert result.pdf_type == PDFType.UNKNOWN
            assert result.error == "PyMuPDF not installed"
            assert result.recommendation == "ai"

    def test_empty_pdf(self):
        mock_doc = MagicMock()
        mock_doc.__len__ = MagicMock(return_value=0)
        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc
        with patch.dict(sys.modules, {"fitz": mock_fitz}):
            result = detect_pdf_type(b"fake pdf bytes")
        assert result.pdf_type == PDFType.EMPTY
        assert result.page_count == 0


@pytest.mark.unit
class TestFastPDFExtractorInit:
    """Test FastPDFExtractor initialization."""

    def test_default_config(self):
        extractor = FastPDFExtractor()
        assert extractor.anthropic_client is None
        assert extractor.min_chars_per_page == MIN_CHARS_PER_PAGE
        assert extractor.always_try_fast is True
        assert extractor._anthropic_config["use_direct_api"] is False

    def test_with_client(self):
        mock_client = MagicMock()
        extractor = FastPDFExtractor(anthropic_client=mock_client)
        assert extractor.anthropic_client == mock_client
        assert extractor._anthropic_config["use_direct_api"] is True
        assert extractor._anthropic_config["source"] == "legacy"

    def test_with_custom_config(self):
        config = {"use_direct_api": False, "source": "custom"}
        extractor = FastPDFExtractor(anthropic_config=config)
        assert extractor._anthropic_config == config

    def test_custom_min_chars(self):
        extractor = FastPDFExtractor(min_chars_per_page=200)
        assert extractor.min_chars_per_page == 200


@pytest.mark.unit
class TestExtractTextFast:
    """Test extract_text_fast with mocked fitz."""

    @patch("fast_pdf_extractor._module_logger")
    def test_no_fitz(self, mock_logger):
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__
        def mock_import(name, *args, **kwargs):
            if name == "fitz":
                raise ImportError("No module named 'fitz'")
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=mock_import):
            pages, success = extract_text_fast(b"fake")
        assert pages == []
        assert success is False

    def test_successful_extraction(self):
        mock_page = MagicMock()
        mock_page.get_text.return_value = "Page content here"
        mock_doc = MagicMock()
        mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))
        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc
        with patch.dict(sys.modules, {"fitz": mock_fitz}):
            pages, success = extract_text_fast(b"fake pdf")
        assert success is True
        assert len(pages) == 1
        assert "[Page 1]" in pages[0]["text"]

    def test_without_page_numbers(self):
        mock_page = MagicMock()
        mock_page.get_text.return_value = "Content"
        mock_doc = MagicMock()
        mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))
        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc
        with patch.dict(sys.modules, {"fitz": mock_fitz}):
            pages, success = extract_text_fast(b"fake", include_page_numbers=False)
        assert success is True
        assert "[Page" not in pages[0]["text"]


class _FakeRescueConfig:
    """Stands in for config so rescue tests are deterministic regardless of .env overrides."""

    def __init__(self, rescue=True, min_chars=50, min_drawings=10):
        self.DOC_HYBRID_BLANK_PAGE_RESCUE = rescue
        self.DOC_HYBRID_BLANK_PAGE_MIN_CHARS = min_chars
        self.DOC_HYBRID_BLANK_PAGE_MIN_DRAWINGS = min_drawings


@pytest.mark.unit
class TestClassifyPageNeedsAI:
    """Per-page router, including the blank-page rescue (silent page-loss fix).

    The rescue targets flattened/vector-outlined text pages: 0 embedded images,
    ~0 extractable characters, hundreds of drawing ops. Before the fix those pages
    were classified 'fast', extracted to "", and stored empty with no warning.
    """

    @staticmethod
    def _page(images=0, chars=0, drawings=0, raise_on_images=False):
        page = MagicMock()
        if raise_on_images:
            page.get_images.side_effect = RuntimeError("cannot inspect page")
        else:
            page.get_images.return_value = [("img",)] * images
        page.get_text.return_value = "x" * chars
        page.get_drawings.return_value = [{"items": []}] * drawings
        return page

    def _classify(self, page, cfg=None):
        with patch.dict(sys.modules, {"config": cfg or _FakeRescueConfig()}):
            return classify_page_needs_ai(page)

    def test_image_page_routes_to_ai(self):
        assert self._classify(self._page(images=1)) is True

    def test_text_page_routes_fast(self):
        assert self._classify(self._page(chars=500, drawings=0)) is False

    def test_text_page_with_heavy_vector_decoration_stays_fast(self):
        # e.g. a dense table page: plenty of real text plus hundreds of rule lines
        assert self._classify(self._page(chars=800, drawings=400)) is False

    def test_flattened_page_rescued_to_ai(self):
        # The confirmed lost class: 0 images, 0 chars, heavy vector ink
        assert self._classify(self._page(chars=0, drawings=1200)) is True

    def test_short_text_with_heavy_ink_rescued_to_ai(self):
        # Outlined body with a surviving real-text footer
        assert self._classify(self._page(chars=10, drawings=400)) is True

    def test_genuinely_blank_page_stays_fast(self):
        assert self._classify(self._page(chars=0, drawings=0)) is False

    def test_light_ink_below_threshold_stays_fast(self):
        # A letterhead rule or border box is not content
        assert self._classify(self._page(chars=0, drawings=3)) is False

    def test_kill_switch_restores_old_routing(self):
        cfg = _FakeRescueConfig(rescue=False)
        assert self._classify(self._page(chars=0, drawings=1200), cfg=cfg) is False

    def test_thresholds_respected(self):
        cfg = _FakeRescueConfig(min_chars=5, min_drawings=500)
        assert self._classify(self._page(chars=0, drawings=400), cfg=cfg) is False
        assert self._classify(self._page(chars=0, drawings=600), cfg=cfg) is True

    def test_config_unavailable_falls_back_to_safe_defaults(self):
        # import config fails -> defaults (rescue ON, 50 chars, 10 drawings)
        with patch.dict(sys.modules, {"config": None}):
            assert classify_page_needs_ai(self._page(chars=0, drawings=1200)) is True
            assert classify_page_needs_ai(self._page(chars=500, drawings=0)) is False

    def test_error_defaults_to_ai(self):
        assert self._classify(self._page(raise_on_images=True)) is True


@pytest.mark.unit
class TestWarnBlankPages:
    """Any page about to be stored with no usable text must be flagged loudly."""

    def test_warns_with_stable_tag_and_page_numbers(self):
        extractor = FastPDFExtractor(logger=MagicMock())
        pages = [
            {"page_number": 1, "text": "[Page 1]\nreal content"},
            {"page_number": 2, "text": ""},
            {"page_number": 3, "text": "   "},
        ]
        out = extractor._warn_blank_pages(r"C:\somewhere\lease.pdf", pages)
        assert out is pages  # passthrough, safe to wrap returns
        extractor.logger.warning.assert_called_once()
        message = extractor.logger.warning.call_args[0][0]
        assert "BLANK_PAGE_STORED" in message
        assert "lease.pdf" in message
        assert "[2, 3]" in message

    def test_silent_when_every_page_has_text(self):
        extractor = FastPDFExtractor(logger=MagicMock())
        pages = [{"page_number": 1, "text": "a"}, {"page_number": 2, "text": "b"}]
        extractor._warn_blank_pages("x.pdf", pages)
        extractor.logger.warning.assert_not_called()

    def test_never_raises(self):
        extractor = FastPDFExtractor(logger=MagicMock())
        # Malformed page entries must not break extraction
        out = extractor._warn_blank_pages("x.pdf", [{"text": None}, {}, {"page_number": 5, "text": ""}])
        assert isinstance(out, list)
