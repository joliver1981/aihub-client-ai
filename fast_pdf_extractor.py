# fast_pdf_extractor.py
"""
Fast PDF Text Extraction Module
===============================
Provides optimized PDF text extraction by detecting document type and routing
to the fastest appropriate extraction method.

Strategy:
1. Detect PDF type (text-based, scanned, or mixed)
2. For text-based PDFs: Use fast PyMuPDF extraction
3. For scanned/mixed PDFs: Use AI-based extraction (Claude Vision)
4. Always fall back to AI extraction if fast methods fail or produce poor results

Dependencies:
    pip install PyMuPDF

Usage:
    from fast_pdf_extractor import FastPDFExtractor
    
    extractor = FastPDFExtractor(anthropic_client, logger)
    pages = extractor.extract_from_pdf(
        file_path="document.pdf",
        document_type="invoice"
    )
"""

import os
import io
import logging
from logging.handlers import WatchedFileHandler
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from CommonUtils import rotate_logs_on_startup, get_log_path


# Configure logging
def setup_logging():
    """Configure logging for fast PDF extractor"""
    logger = logging.getLogger("FastPDFExtractor")
    log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
    log_level = getattr(logging, log_level_name, logging.DEBUG)
    logger.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler = WatchedFileHandler(filename=os.getenv('FAST_PDF_EXTRACTOR_LOG', get_log_path('fast_pdf_extractor.txt')), encoding='utf-8')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


rotate_logs_on_startup(os.getenv('FAST_PDF_EXTRACTOR_LOG', get_log_path('fast_pdf_extractor.txt')))
_module_logger = setup_logging()


class PDFType(Enum):
    """PDF document types based on content analysis"""
    TEXT = "text"           # Native text-based PDF
    SCANNED = "scanned"     # Scanned/image-based PDF
    MIXED = "mixed"         # Mixed content (some pages scanned, some text)
    EMPTY = "empty"         # Empty PDF
    UNKNOWN = "unknown"     # Could not determine type


class ExtractionMethod(Enum):
    """Methods used for text extraction"""
    PYMUPDF_DIRECT = "pymupdf_direct"                       # Fast PyMuPDF extraction
    AI_VISION = "ai_vision"                                 # Claude Vision API
    PYMUPDF_WITH_AI_FALLBACK = "pymupdf_with_ai_fallback"   # Started with PyMuPDF, fell back to AI
    HYBRID = "hybrid"                                       # Per-page routing

@dataclass
class PDFAnalysis:
    """Results of PDF type analysis"""
    pdf_type: PDFType
    confidence: float
    page_count: int
    avg_chars_per_page: float
    sampled_pages: int
    scanned_pages_in_sample: int
    has_fonts: bool
    has_images: bool
    recommendation: str  # 'direct' or 'ocr'
    error: Optional[str] = None


@dataclass
class ExtractionResult:
    """Results of PDF text extraction"""
    pages: List[Dict[str, Any]]
    method_used: ExtractionMethod
    pdf_analysis: PDFAnalysis
    fast_extraction_attempted: bool
    fast_extraction_success: bool
    fallback_reason: Optional[str] = None
    # Hybrid extraction stats
    fast_page_count: Optional[int] = None
    ai_page_count: Optional[int] = None


# Minimum thresholds for fast extraction quality
MIN_CHARS_PER_PAGE = 100  # Minimum expected characters for a valid page
MIN_EXTRACTION_RATIO = 0.7  # If fast extraction gets < 70% of expected chars, use AI


def detect_pdf_type(pdf_bytes: bytes) -> PDFAnalysis:
    """
    Analyze a PDF to determine if it's scanned, text-based, or mixed.
    
    Args:
        pdf_bytes: Raw PDF bytes
        
    Returns:
        PDFAnalysis dataclass with analysis results
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return PDFAnalysis(
            pdf_type=PDFType.UNKNOWN,
            confidence=0.0,
            page_count=0,
            avg_chars_per_page=0,
            sampled_pages=0,
            scanned_pages_in_sample=0,
            has_fonts=False,
            has_images=False,
            recommendation='ai',
            error='PyMuPDF not installed'
        )
    
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_count = len(doc)
        
        if page_count == 0:
            doc.close()
            return PDFAnalysis(
                pdf_type=PDFType.EMPTY,
                confidence=1.0,
                page_count=0,
                avg_chars_per_page=0,
                sampled_pages=0,
                scanned_pages_in_sample=0,
                has_fonts=False,
                has_images=False,
                recommendation='none'
            )
        
        # Sample pages strategically (first, last, middle, quarters) - max 5
        sample_indices = [0]
        if page_count > 1:
            sample_indices.append(page_count - 1)
        if page_count > 2:
            sample_indices.append(page_count // 2)
        if page_count > 6:
            sample_indices.extend([page_count // 4, 3 * page_count // 4])
        sample_indices = sorted(set(sample_indices))[:5]
        
        total_text_chars = 0
        total_fonts = 0
        total_images = 0
        scanned_pages = 0
        
        for page_num in sample_indices:
            page = doc[page_num]
            
            # Get text content
            text = page.get_text().strip()
            text_chars = len(text)
            total_text_chars += text_chars
            
            # Count fonts
            try:
                total_fonts += len(page.get_fonts())
            except Exception:
                pass
            
            # Analyze images and their coverage
            try:
                images = page.get_images()
                total_images += len(images)
                
                page_area = page.rect.width * page.rect.height
                image_area = 0
                for img in images:
                    try:
                        for rect in page.get_image_rects(img[0]):
                            image_area += rect.width * rect.height
                    except Exception:
                        pass
                image_coverage = image_area / page_area if page_area > 0 else 0
            except Exception:
                image_coverage = 0
            
            # Heuristic: page is scanned if it has high image coverage but little text
            if (image_coverage > 0.7 and text_chars < 100) or (text_chars < 50 and total_images > 0):
                scanned_pages += 1
        
        doc.close()
        
        # Calculate metrics
        sampled_count = len(sample_indices)
        scanned_ratio = scanned_pages / sampled_count if sampled_count > 0 else 0
        avg_chars_per_page = total_text_chars / sampled_count if sampled_count > 0 else 0
        
        # Determine PDF type and recommendation
        if scanned_ratio >= 0.7:
            pdf_type, confidence, recommendation = PDFType.SCANNED, scanned_ratio, 'ai'
        elif scanned_ratio >= 0.3:
            pdf_type, confidence, recommendation = PDFType.MIXED, 0.7, 'ai'
        elif avg_chars_per_page < 50 and total_images > 0:
            pdf_type, confidence, recommendation = PDFType.SCANNED, 0.7, 'ai'
        else:
            pdf_type, confidence, recommendation = PDFType.TEXT, 1 - scanned_ratio, 'direct'
        
        return PDFAnalysis(
            pdf_type=pdf_type,
            confidence=round(confidence, 2),
            page_count=page_count,
            avg_chars_per_page=round(avg_chars_per_page, 0),
            sampled_pages=sampled_count,
            scanned_pages_in_sample=scanned_pages,
            has_fonts=total_fonts > 0,
            has_images=total_images > 0,
            recommendation=recommendation
        )
        
    except Exception as e:
        return PDFAnalysis(
            pdf_type=PDFType.UNKNOWN,
            confidence=0.0,
            page_count=0,
            avg_chars_per_page=0,
            sampled_pages=0,
            scanned_pages_in_sample=0,
            has_fonts=False,
            has_images=False,
            recommendation='ai',
            error=str(e)
        )


def extract_text_fast(pdf_bytes: bytes, include_page_numbers: bool = True) -> Tuple[List[Dict[str, Any]], bool]:
    """
    Extract text from PDF using PyMuPDF (fast, no AI).
    
    Args:
        pdf_bytes: Raw PDF bytes
        include_page_numbers: Whether to prefix text with page numbers
        
    Returns:
        Tuple of (list of page dicts, success boolean)
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        _module_logger.error("PyMuPDF not installed, cannot use fast extraction")
        return [], False
    
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages = []
        
        for page_num, page in enumerate(doc, 1):
            page_text = page.get_text("text").strip()
            
            if include_page_numbers and page_text:
                page_text = f"[Page {page_num}]\n{page_text}"
            
            pages.append({
                "page_number": page_num,
                "text": page_text
            })
        
        doc.close()
        return pages, True
        
    except Exception as e:
        _module_logger.error(f"Fast PDF extraction failed: {e}")
        return [], False


def validate_extraction_quality(
    pages: List[Dict[str, Any]], 
    analysis: PDFAnalysis,
    min_chars_per_page: int = MIN_CHARS_PER_PAGE
) -> Tuple[bool, str]:
    """
    Validate whether fast extraction produced acceptable results.
    
    Args:
        pages: Extracted pages
        analysis: PDF analysis results
        min_chars_per_page: Minimum expected characters per page
        
    Returns:
        Tuple of (is_valid, reason_if_invalid)
    """
    if not pages:
        return False, "No pages extracted"
    
    # Calculate average characters extracted
    total_chars = sum(len(p.get("text", "")) for p in pages)
    avg_chars = total_chars / len(pages) if pages else 0
    
    # Check if extraction produced meaningful content
    if avg_chars < min_chars_per_page:
        # If we expected text (based on analysis) but got little, it's a problem
        if analysis.avg_chars_per_page > min_chars_per_page:
            return False, f"Extracted avg {avg_chars:.0f} chars/page, expected ~{analysis.avg_chars_per_page:.0f}"
        # If analysis also showed little text, might be scanned
        elif analysis.has_images:
            return False, "Low text extraction with images present - likely scanned"
    
    # Check for completely empty pages that shouldn't be empty
    empty_pages = sum(1 for p in pages if len(p.get("text", "").strip()) < 20)
    empty_ratio = empty_pages / len(pages) if pages else 1
    
    if empty_ratio > 0.5 and analysis.page_count > 1:
        return False, f"{empty_pages}/{len(pages)} pages are nearly empty"
    
    return True, ""

def classify_page_needs_ai(page) -> bool:
    """
    Determine if a page needs AI extraction.
    
    Simple rule: if the page has ANY images, use AI.
    
    Args:
        page: A PyMuPDF page object
        
    Returns:
        True if page needs AI extraction, False if fast extraction is sufficient
    """
    try:
        images = page.get_images()
        return len(images) > 0
    except Exception:
        # If we can't determine, default to AI (safer)
        return True

class FastPDFExtractor:
    """
    Optimized PDF text extractor that intelligently routes between
    fast PyMuPDF extraction and AI-based extraction.
    """
    
    def __init__(
        self, 
        anthropic_client=None, 
        logger=None,
        anthropic_config=None,
        anthropic_proxy_client=None,
        min_chars_per_page: int = MIN_CHARS_PER_PAGE,
        always_try_fast: bool = True
    ):
        """
        Initialize the fast PDF extractor.
        
        Args:
            anthropic_client: Initialized Anthropic API client (for AI fallback)
            logger: Optional logger instance
            anthropic_config: Configuration dict from get_anthropic_config()
            anthropic_proxy_client: AnthropicProxyClient instance for proxy mode
            min_chars_per_page: Minimum characters expected per page for quality validation
            always_try_fast: If True, always try fast extraction first even for scanned PDFs
        """
        self.anthropic_client = anthropic_client
        self.logger = logger or _module_logger
        self.anthropic_proxy_client = anthropic_proxy_client
        self.min_chars_per_page = min_chars_per_page
        self.always_try_fast = always_try_fast
        
        # Store config, default to direct API if client provided and no config
        if anthropic_config is None:
            self._anthropic_config = {
                'use_direct_api': anthropic_client is not None,
                'source': 'legacy'
            }
        else:
            self._anthropic_config = anthropic_config
    
    def extract_from_pdf(
        self,
        file_path: str,
        document_type: str = "document",
        use_batch_processing: bool = False,
        batch_size: int = 3,
        include_page_numbers: bool = True,
        force_ai: bool = False,
        force_fast: bool = False
    ) -> List[Dict[str, Any]]:
        
        self.logger.info(f"FastPDFExtractor processing: {file_path}")
        
        if force_ai:
            # Forced AI extraction for all pages
            with open(file_path, 'rb') as f:
                pdf_bytes = f.read()
            return self._extract_with_ai(
                file_path=file_path,
                pdf_bytes=pdf_bytes,
                document_type=document_type,
                use_batch_processing=use_batch_processing,
                batch_size=batch_size,
                include_page_numbers=include_page_numbers
            )
        
        if force_fast:
            # Forced fast extraction (no AI fallback)
            with open(file_path, 'rb') as f:
                pdf_bytes = f.read()
            fast_pages, success = extract_text_fast(pdf_bytes, include_page_numbers)
            if success:
                return fast_pages
            return [{"page_number": i + 1, "text": ""} for i in range(len(fast_pages) or 1)]
        
        # Default: hybrid extraction (per-page routing)
        return self.extract_hybrid(
            file_path=file_path,
            document_type=document_type,
            include_page_numbers=include_page_numbers
        )
    
    def _extract_with_ai(
        self,
        file_path: str,
        pdf_bytes: bytes,
        document_type: str,
        use_batch_processing: bool,
        batch_size: int,
        include_page_numbers: bool
    ) -> List[Dict[str, Any]]:
        """
        Extract text using AI/LLM (Claude Vision API).
        
        This delegates to the existing MultiPagePDFHandler for compatibility.
        """
        # Import here to avoid circular dependency
        from LLMDocumentEngine import MultiPagePDFHandler
        
        pdf_handler = MultiPagePDFHandler(
            self.anthropic_client,
            self.logger,
            anthropic_config=self._anthropic_config,
            anthropic_proxy_client=self.anthropic_proxy_client
        )
        
        return pdf_handler.extract_from_pdf(
            file_path,
            document_type=document_type,
            use_batch_processing=use_batch_processing,
            batch_size=batch_size,
            include_page_numbers=include_page_numbers
        )
    
    def extract_with_details(
        self,
        file_path: str,
        document_type: str = "document",
        use_batch_processing: bool = False,
        batch_size: int = 3,
        include_page_numbers: bool = True,
        force_ai: bool = False,
        force_fast: bool = False
    ) -> ExtractionResult:
        """
        Extract text from PDF with detailed results about the extraction process.
        
        Returns:
            ExtractionResult dataclass with pages and extraction metadata
        """
        self.logger.info(f"FastPDFExtractor processing (with details): {file_path}")
        
        with open(file_path, 'rb') as f:
            pdf_bytes = f.read()
        
        analysis = detect_pdf_type(pdf_bytes)
        
        if force_ai:
            pages = self._extract_with_ai(
                file_path=file_path,
                pdf_bytes=pdf_bytes,
                document_type=document_type,
                use_batch_processing=use_batch_processing,
                batch_size=batch_size,
                include_page_numbers=include_page_numbers
            )
            return ExtractionResult(
                pages=pages,
                method_used=ExtractionMethod.AI_VISION,
                pdf_analysis=analysis,
                fast_extraction_attempted=False,
                fast_extraction_success=False,
                fast_page_count=0,
                ai_page_count=len(pages)
            )
        
        if force_fast:
            fast_pages, success = extract_text_fast(pdf_bytes, include_page_numbers)
            return ExtractionResult(
                pages=fast_pages if success else [],
                method_used=ExtractionMethod.PYMUPDF_DIRECT,
                pdf_analysis=analysis,
                fast_extraction_attempted=True,
                fast_extraction_success=success,
                fallback_reason=None if success else "Fast extraction failed",
                fast_page_count=len(fast_pages) if success else 0,
                ai_page_count=0
            )
        
        # Default: hybrid extraction (per-page routing)
        return self.extract_hybrid_with_details(
            file_path=file_path,
            document_type=document_type,
            include_page_numbers=include_page_numbers
        )

    def extract_hybrid(
        self,
        file_path: str,
        document_type: str = "document",
        include_page_numbers: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Extract text using per-page routing: fast extraction for text pages,
        AI extraction for pages with images.
        
        Args:
            file_path: Path to the PDF file
            document_type: Type of document for AI context
            include_page_numbers: Whether to prefix text with page numbers
            
        Returns:
            List of dictionaries with page_number and text
        """
        try:
            import fitz
        except ImportError:
            self.logger.warning("PyMuPDF not available, falling back to full AI extraction")
            return self._extract_with_ai(
                file_path=file_path,
                pdf_bytes=open(file_path, 'rb').read(),
                document_type=document_type,
                use_batch_processing=False,
                batch_size=1,
                include_page_numbers=include_page_numbers
            )
        
        self.logger.info(f"FastPDFExtractor hybrid processing: {file_path}")
        
        # Read PDF with PyMuPDF for page analysis
        with open(file_path, 'rb') as f:
            pdf_bytes = f.read()
        
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)
        
        # First pass: classify all pages
        page_needs_ai = []
        for page_num in range(total_pages):
            needs_ai = classify_page_needs_ai(doc[page_num])
            page_needs_ai.append(needs_ai)
        
        doc.close()
        
        text_page_count = page_needs_ai.count(False)
        ai_page_count = page_needs_ai.count(True)
        
        self.logger.info(
            f"Page classification: {text_page_count} text-only, {ai_page_count} need AI"
        )
        
        # If all pages need AI, just use the existing full AI extraction
        if text_page_count == 0:
            self.logger.info("All pages need AI, using standard AI extraction")
            return self._extract_with_ai(
                file_path=file_path,
                pdf_bytes=pdf_bytes,
                document_type=document_type,
                use_batch_processing=False,
                batch_size=1,
                include_page_numbers=include_page_numbers
            )
        
        # If no pages need AI, use fast extraction
        if ai_page_count == 0:
            self.logger.info("No pages need AI, using fast extraction")
            fast_pages, success = extract_text_fast(pdf_bytes, include_page_numbers)
            if success:
                return fast_pages
        
        # Hybrid extraction: mix of fast and AI
        from LLMDocumentEngine import MultiPagePDFHandler
        
        pdf_handler = MultiPagePDFHandler(
            self.anthropic_client,
            self.logger,
            anthropic_config=self._anthropic_config,
            anthropic_proxy_client=self.anthropic_proxy_client
        )
        
        # Re-open for extraction
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages = []
        
        for page_num in range(total_pages):
            page_number_display = page_num + 1  # 1-indexed for display
            
            if page_needs_ai[page_num]:
                # Use AI extraction for this page via MultiPagePDFHandler
                self.logger.debug(f"Page {page_number_display}: using AI extraction (has images)")
                
                # Create single-page PDF bytes
                single_page_pdf = pdf_handler._create_subset_pdf(file_path, [page_num])
                
                # Extract using existing Claude method
                page_result = pdf_handler._extract_single_page_with_claude(
                    pdf_bytes=single_page_pdf,
                    document_type=document_type,
                    page_number=page_number_display,
                    include_page_numbers=include_page_numbers
                )
            else:
                # Use fast extraction for this page
                self.logger.debug(f"Page {page_number_display}: using fast extraction (text only)")
                page_text = doc[page_num].get_text("text").strip()
                
                if include_page_numbers and page_text:
                    page_text = f"[Page {page_number_display}]\n{page_text}"
                
                page_result = {
                    "page_number": page_number_display,
                    "text": page_text
                }
            
            pages.append(page_result)
        
        doc.close()
        
        self.logger.info(
            f"Hybrid extraction complete: {len(pages)} pages "
            f"({text_page_count} fast, {ai_page_count} AI)"
        )
        
        return pages
    
    def extract_hybrid_with_details(
    self,
    file_path: str,
    document_type: str = "document",
    include_page_numbers: bool = True
) -> ExtractionResult:
        """
        Extract text using per-page hybrid routing, returning detailed results.
        
        Args:
            file_path: Path to the PDF file
            document_type: Type of document for AI context
            include_page_numbers: Whether to prefix text with page numbers
            
        Returns:
            ExtractionResult with pages and extraction metadata
        """
        try:
            import fitz
        except ImportError:
            # Fall back to full AI extraction
            with open(file_path, 'rb') as f:
                pdf_bytes = f.read()
            analysis = detect_pdf_type(pdf_bytes)
            pages = self._extract_with_ai(
                file_path=file_path,
                pdf_bytes=pdf_bytes,
                document_type=document_type,
                use_batch_processing=False,
                batch_size=1,
                include_page_numbers=include_page_numbers
            )
            return ExtractionResult(
                pages=pages,
                method_used=ExtractionMethod.AI_VISION,
                pdf_analysis=analysis,
                fast_extraction_attempted=False,
                fast_extraction_success=False,
                fallback_reason="PyMuPDF not available",
                fast_page_count=0,
                ai_page_count=len(pages)
            )
        
        self.logger.info(f"FastPDFExtractor hybrid processing (with details): {file_path}")
        
        with open(file_path, 'rb') as f:
            pdf_bytes = f.read()
        
        analysis = detect_pdf_type(pdf_bytes)
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)
        
        # Classify all pages
        page_needs_ai = []
        for page_num in range(total_pages):
            needs_ai = classify_page_needs_ai(doc[page_num])
            page_needs_ai.append(needs_ai)
        
        doc.close()
        
        text_page_count = page_needs_ai.count(False)
        ai_page_count = page_needs_ai.count(True)
        
        self.logger.info(f"Page classification: {text_page_count} text-only, {ai_page_count} need AI")
        
        # All pages need AI
        if text_page_count == 0:
            pages = self._extract_with_ai(
                file_path=file_path,
                pdf_bytes=pdf_bytes,
                document_type=document_type,
                use_batch_processing=False,
                batch_size=1,
                include_page_numbers=include_page_numbers
            )
            return ExtractionResult(
                pages=pages,
                method_used=ExtractionMethod.AI_VISION,
                pdf_analysis=analysis,
                fast_extraction_attempted=False,
                fast_extraction_success=False,
                fast_page_count=0,
                ai_page_count=ai_page_count
            )
        
        # No pages need AI
        if ai_page_count == 0:
            fast_pages, success = extract_text_fast(pdf_bytes, include_page_numbers)
            if success:
                return ExtractionResult(
                    pages=fast_pages,
                    method_used=ExtractionMethod.PYMUPDF_DIRECT,
                    pdf_analysis=analysis,
                    fast_extraction_attempted=True,
                    fast_extraction_success=True,
                    fast_page_count=text_page_count,
                    ai_page_count=0
                )
        
        # Hybrid extraction
        from LLMDocumentEngine import MultiPagePDFHandler
        
        pdf_handler = MultiPagePDFHandler(
            self.anthropic_client,
            self.logger,
            anthropic_config=self._anthropic_config,
            anthropic_proxy_client=self.anthropic_proxy_client
        )
        
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages = []
        
        for page_num in range(total_pages):
            page_number_display = page_num + 1
            
            if page_needs_ai[page_num]:
                self.logger.debug(f"Page {page_number_display}: using AI extraction")
                single_page_pdf = pdf_handler._create_subset_pdf(file_path, [page_num])
                page_result = pdf_handler._extract_single_page_with_claude(
                    pdf_bytes=single_page_pdf,
                    document_type=document_type,
                    page_number=page_number_display,
                    include_page_numbers=include_page_numbers
                )
            else:
                self.logger.debug(f"Page {page_number_display}: using fast extraction")
                page_text = doc[page_num].get_text("text").strip()
                if include_page_numbers and page_text:
                    page_text = f"[Page {page_number_display}]\n{page_text}"
                page_result = {
                    "page_number": page_number_display,
                    "text": page_text
                }
            
            pages.append(page_result)
        
        doc.close()
        
        self.logger.info(f"Hybrid extraction complete: {len(pages)} pages ({text_page_count} fast, {ai_page_count} AI)")
        
        return ExtractionResult(
            pages=pages,
            method_used=ExtractionMethod.HYBRID,
            pdf_analysis=analysis,
            fast_extraction_attempted=True,
            fast_extraction_success=True,
            fast_page_count=text_page_count,
            ai_page_count=ai_page_count
        )

# Convenience function for simple usage
def extract_pdf_optimized(
    file_path: str,
    document_type: str = "document",
    anthropic_client=None,
    anthropic_config=None,
    anthropic_proxy_client=None,
    include_page_numbers: bool = True,
    logger=None
) -> List[Dict[str, Any]]:
    """
    Convenience function for optimized PDF extraction.
    
    Args:
        file_path: Path to PDF file
        document_type: Type of document
        anthropic_client: Anthropic client for AI fallback
        anthropic_config: Anthropic configuration
        anthropic_proxy_client: Proxy client for API calls
        include_page_numbers: Whether to include page numbers in text
        logger: Optional logger
        
    Returns:
        List of page dictionaries with extracted text
    """
    extractor = FastPDFExtractor(
        anthropic_client=anthropic_client,
        anthropic_config=anthropic_config,
        anthropic_proxy_client=anthropic_proxy_client,
        logger=logger
    )
    
    return extractor.extract_from_pdf(
        file_path=file_path,
        document_type=document_type,
        include_page_numbers=include_page_numbers
    )


# Exports
__all__ = [
    'FastPDFExtractor',
    'PDFType',
    'ExtractionMethod',
    'PDFAnalysis',
    'ExtractionResult',
    'detect_pdf_type',
    'extract_text_fast',
    'validate_extraction_quality',
    'classify_page_needs_ai',
    'extract_pdf_optimized'
]
