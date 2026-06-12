"""
file_detector.py - File type and complexity detector.

Accurate page/row/line counting:
- PDF    → PyPDF2 se actual page count
- DOCX   → python-docx se actual paragraph/page count
- CSV    → actual row count
- Code   → actual line count
"""

import io
import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


# ── File Type Enum ─────────────────────────────────────────────────────────────

class FileType(str, Enum):
    PDF     = "pdf"
    DOCX    = "docx"
    CSV     = "csv"
    CODE    = "code"
    IMAGE   = "image"
    TEXT    = "text"
    UNKNOWN = "unknown"


# ── Extensions ─────────────────────────────────────────────────────────────────

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".cpp", ".c", ".cs", ".go",
    ".rs", ".rb", ".php", ".swift", ".kt",
    ".scala", ".r", ".sql", ".sh", ".yaml",
    ".yml", ".json", ".xml", ".html", ".css",
    ".tf", ".dockerfile", ".env",
}

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif",
    ".bmp", ".webp", ".svg", ".ico",
}


# ── File Analysis Result ───────────────────────────────────────────────────────

@dataclass
class FileAnalysis:
    filename: str
    file_type: FileType
    size_bytes: int
    size_kb: float
    page_count: int = 0
    row_count: int = 0
    line_count: int = 0
    extension: str = ""
    routing_signal: str = "neutral"
    routing_reason: str = ""
    recommended_model: str = ""

    @property
    def size_mb(self) -> float:
        return round(self.size_bytes / (1024 * 1024), 2)


# ── File Detector ──────────────────────────────────────────────────────────────

class FileDetector:
    """
    Analyzes uploaded file and returns FileAnalysis with accurate counts.
    """

    # Routing thresholds
    PDF_LARGE_PAGES   = 50
    DOCX_LARGE_PAGES  = 30
    CSV_LARGE_ROWS    = 1000
    CODE_LARGE_LINES  = 300
    FILE_LARGE_MB     = 5.0

    def __init__(self) -> None:
        logger.debug("FileDetector ready.")

    def analyze(self, filename: str, file_bytes: bytes) -> FileAnalysis:
        ext = self._get_extension(filename)
        file_type = self._detect_type(ext)
        size_bytes = len(file_bytes)
        size_kb = round(size_bytes / 1024, 2)

        analysis = FileAnalysis(
            filename=filename,
            file_type=file_type,
            size_bytes=size_bytes,
            size_kb=size_kb,
            extension=ext,
        )

        # Accurate type-specific analysis
        if file_type == FileType.PDF:
            analysis.page_count = self._count_pdf_pages(file_bytes)

        elif file_type == FileType.DOCX:
            analysis.page_count = self._count_docx_pages(file_bytes)

        elif file_type == FileType.CSV:
            analysis.row_count = self._count_csv_rows(file_bytes)

        elif file_type in (FileType.CODE, FileType.TEXT):
            analysis.line_count = self._count_lines(file_bytes)

        # Apply routing rules
        self._apply_routing_rules(analysis)

        logger.info(
            "File: '%s' type=%s size=%.1fKB pages=%d rows=%d lines=%d signal=%s",
            filename, file_type.value, size_kb,
            analysis.page_count, analysis.row_count,
            analysis.line_count, analysis.routing_signal,
        )
        return analysis

    # ── Routing Rules ──────────────────────────────────────────────────────────

    def _apply_routing_rules(self, analysis: FileAnalysis) -> None:
        ft = analysis.file_type

        # Large file → always premium
        if analysis.size_mb > self.FILE_LARGE_MB:
            analysis.routing_signal = "premium"
            analysis.routing_reason = (
                f"Large file ({analysis.size_mb}MB) requires powerful model"
            )
            analysis.recommended_model = "claude-opus"
            return

        if ft == FileType.PDF:
            if analysis.page_count > self.PDF_LARGE_PAGES:
                analysis.routing_signal = "premium"
                analysis.routing_reason = (
                    f"PDF has {analysis.page_count} pages "
                    f"(>{self.PDF_LARGE_PAGES}) — requires Claude"
                )
                analysis.recommended_model = "claude-opus"
            else:
                analysis.routing_signal = "free"
                analysis.routing_reason = (
                    f"PDF has {analysis.page_count} pages — free model sufficient"
                )
                analysis.recommended_model = "llama-3-8b"

        elif ft == FileType.DOCX:
            if analysis.page_count > self.DOCX_LARGE_PAGES:
                analysis.routing_signal = "premium"
                analysis.routing_reason = (
                    f"Large document ({analysis.page_count} pages) — requires Claude"
                )
                analysis.recommended_model = "claude-opus"
            else:
                analysis.routing_signal = "free"
                analysis.routing_reason = (
                    f"Small document ({analysis.page_count} pages) "
                    f"— free model sufficient"
                )
                analysis.recommended_model = "mistral-7b"

        elif ft == FileType.CSV:
            analysis.routing_signal = "premium"
            analysis.routing_reason = (
                f"CSV data analysis ({analysis.row_count} rows) — routed to GPT"
            )
            analysis.recommended_model = "gpt-5"

        elif ft == FileType.CODE:
            analysis.routing_signal = "premium"
            analysis.routing_reason = (
                f"Code file ({analysis.line_count} lines) — routed to Claude"
            )
            analysis.recommended_model = "claude-opus"

        elif ft == FileType.IMAGE:
            analysis.routing_signal = "free"
            analysis.routing_reason = "Image file — free model sufficient"
            analysis.recommended_model = "llama-3-8b"

        else:
            analysis.routing_signal = "neutral"
            analysis.routing_reason = "Unknown file type — using prompt for routing"
            analysis.recommended_model = ""

    # ── Type Detection ─────────────────────────────────────────────────────────

    def _detect_type(self, ext: str) -> FileType:
        if ext == ".pdf":
            return FileType.PDF
        elif ext in (".docx", ".doc"):
            return FileType.DOCX
        elif ext in (".csv", ".tsv"):
            return FileType.CSV
        elif ext in CODE_EXTENSIONS:
            return FileType.CODE
        elif ext in IMAGE_EXTENSIONS:
            return FileType.IMAGE
        elif ext in (".txt", ".md", ".rst"):
            return FileType.TEXT
        return FileType.UNKNOWN

    def _get_extension(self, filename: str) -> str:
        if "." in filename:
            return "." + filename.rsplit(".", 1)[-1].lower()
        return ""

    # ── PDF Page Count — PyPDF2 ────────────────────────────────────────────────

    def _count_pdf_pages(self, file_bytes: bytes) -> int:
        """Accurate PDF page count using PyPDF2."""
        try:
            import PyPDF2
            reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            count = len(reader.pages)
            logger.debug("PDF pages (PyPDF2): %d", count)
            return count
        except ImportError:
            logger.warning("PyPDF2 not installed — trying raw scan.")
            return self._count_pdf_pages_raw(file_bytes)
        except Exception as e:
            logger.warning("PyPDF2 failed: %s — trying raw scan.", e)
            return self._count_pdf_pages_raw(file_bytes)

    def _count_pdf_pages_raw(self, file_bytes: bytes) -> int:
        """Fallback: scan PDF bytes for /Type /Page markers."""
        try:
            content = file_bytes.decode("latin-1", errors="ignore")
            count = content.count("/Type /Page")
            if count == 0:
                count = content.count("/Type/Page")
            if count == 0:
                # Last resort: estimate from size
                count = max(1, len(file_bytes) // 51200)
            logger.debug("PDF pages (raw scan): %d", count)
            return count
        except Exception as e:
            logger.warning("PDF raw scan failed: %s", e)
            return 1

    # ── DOCX Page Count — python-docx ─────────────────────────────────────────

    def _count_docx_pages(self, file_bytes: bytes) -> int:
        """
        Accurate DOCX page count using python-docx.

        Strategy (in order):
        1. Read app.xml for actual page count (most accurate)
        2. Count page break markers in document
        3. Estimate from paragraph/word count
        """
        try:
            from docx import Document
            import zipfile

            # Strategy 1: Read app.xml — has actual page count
            try:
                with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
                    if "docProps/app.xml" in z.namelist():
                        app_xml = z.read("docProps/app.xml").decode("utf-8", errors="ignore")
                        # Find <Pages>N</Pages>
                        import re
                        match = re.search(r"<Pages>(\d+)</Pages>", app_xml)
                        if match:
                            count = int(match.group(1))
                            logger.debug("DOCX pages (app.xml): %d", count)
                            return count
            except Exception as e:
                logger.debug("app.xml read failed: %s", e)

            # Strategy 2: Count page breaks in document
            doc = Document(io.BytesIO(file_bytes))
            page_breaks = 0

            for para in doc.paragraphs:
                for run in para.runs:
                    if run._element.xml and "lastRenderedPageBreak" in run._element.xml:
                        page_breaks += 1
                    if run._element.xml and 'type="page"' in run._element.xml:
                        page_breaks += 1

            if page_breaks > 0:
                count = page_breaks + 1
                logger.debug("DOCX pages (page breaks): %d", count)
                return count

            # Strategy 3: Estimate from word count
            total_words = sum(
                len(para.text.split())
                for para in doc.paragraphs
                if para.text.strip()
            )
            # ~250 words per page average
            count = max(1, round(total_words / 250))
            logger.debug(
                "DOCX pages (word estimate): %d words → %d pages",
                total_words, count,
            )
            return count

        except ImportError:
            logger.warning("python-docx not installed — using size estimate.")
            return self._estimate_docx_from_size(file_bytes)
        except Exception as e:
            logger.warning("DOCX page count failed: %s — using size estimate.", e)
            return self._estimate_docx_from_size(file_bytes)

    def _estimate_docx_from_size(self, file_bytes: bytes) -> int:
        """Last resort: estimate DOCX pages from file size."""
        # ~15KB per page for typical DOCX
        count = max(1, len(file_bytes) // 15360)
        logger.debug("DOCX pages (size estimate): %d", count)
        return count

    # ── CSV Row Count ──────────────────────────────────────────────────────────

    def _count_csv_rows(self, file_bytes: bytes) -> int:
        """Accurate CSV row count."""
        try:
            content = file_bytes.decode("utf-8", errors="ignore")
            lines = [l for l in content.splitlines() if l.strip()]
            # Subtract header row
            count = max(0, len(lines) - 1)
            logger.debug("CSV rows: %d", count)
            return count
        except Exception as e:
            logger.warning("CSV row count failed: %s", e)
            return 0

    # ── Line Count ─────────────────────────────────────────────────────────────

    def _count_lines(self, file_bytes: bytes) -> int:
        """Count non-empty lines in code/text file."""
        try:
            content = file_bytes.decode("utf-8", errors="ignore")
            lines = [l for l in content.splitlines() if l.strip()]
            logger.debug("Line count: %d", len(lines))
            return len(lines)
        except Exception as e:
            logger.warning("Line count failed: %s", e)
            return 0