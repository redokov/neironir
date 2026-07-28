"""Background pipeline that drives a single :class:`Job` to completion.

The pipeline is a sequence of side-effecting steps orchestrated by
:func:`run_job`. It is intentionally linear: each step feeds the next,
and any error short-circuits the rest of the run and flips the job to
``FAILED``.

Starting from Phase 1 the pipeline also persists:

* The extracted plain text as ``extracted_text.txt`` for the feedback UI.
* The detected entity spans as ``annotations.json`` for the review step.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from uuid import UUID

from neironir.config import Settings
from neironir.converters.base import DocumentConverter, Replacement
from neironir.converters.docx import DocxConverter
from neironir.converters.markdown import MarkdownConverter
from neironir.domain.job import JobStatus
from neironir.domain.placeholder import PlaceholderCounter
from neironir.privacy.client import EntitySpan, PrivacyFilterClient
from neironir.storage.local import LocalStorage

logger = logging.getLogger(__name__)


# Format converter registry, keyed by the file extension stored on the
# :class:`Job`. Adding a new format is a matter of registering a new
# converter here.
CONVERTERS: dict[str, DocumentConverter] = {
    "md": MarkdownConverter(),
    "docx": DocxConverter(),
}


async def run_job(
    job_id: UUID,
    *,
    settings: Settings,
    storage: LocalStorage,
    privacy: PrivacyFilterClient,
) -> None:
    """Execute the full pipeline for a single ``job_id``.

    The function is safe to call from FastAPI's ``BackgroundTasks``: it
    owns no cross-job state and persists intermediate state via
    ``storage.save_job`` so a process crash leaves a recoverable record.

    Conversion direction:

    * ``md`` → ``md`` — markdown round-trip.
    * ``docx`` → ``docx`` — docx round-trip (paragraphs + tables).
    * ``docx`` → ``md`` — docx is converted to markdown text first (via
      pandoc if available, else via the python-docx flat-text fallback),
      the redaction is applied to the extracted text, and the result
      is written back as a ``.md`` file.

    ``md`` → ``docx`` is **not** supported in MVP and raises ``ValueError``.
    """
    job = storage.load_job(job_id)
    try:
        job.status = JobStatus.PROCESSING
        storage.save_job(job)

        source_ext = job.source_ext
        output_ext = job.effective_output_ext
        _validate_conversion(source_ext, output_ext)

        source_converter = _converter_for(source_ext)
        target_converter = _converter_for(output_ext)

        source_path = storage.job_dir(job_id) / f"source.{source_ext}"

        # For ``docx → md`` we use pandoc to preserve headings, lists
        # and emphasis in the output.  The markdown text is also what
        # the privacy filter sees for annotation.
        if source_ext == "docx" and output_ext == "md":
            text = _docx_to_markdown(source_path)
        else:
            text = source_converter.extract_text(source_path)

        # Persist the extracted text for the feedback UI.
        _save_extracted_text(storage, job_id, text)

        spans = await privacy.annotate(text)

        # Persist annotations for the feedback UI.
        _save_annotations(storage, job_id, spans, text)

        replacements = _build_replacements(spans)

        target_path = storage.job_dir(job_id) / f"result.{output_ext}"

        # For ``docx → md`` we cannot point the MarkdownConverter at
        # the .docx file (it would read the binary as UTF-8).  Write
        # the extracted markdown into a sibling ``extracted.md`` and
        # use that as the source.
        if source_ext == "docx" and output_ext == "md":
            md_source_path = storage.job_dir(job_id) / "extracted.md"
            md_source_path.write_text(text, encoding="utf-8")
            target_converter.build(md_source_path, target_path, replacements)
        else:
            target_converter.build(source_path, target_path, replacements)

        job.status = JobStatus.COMPLETED
        job.finished_at = datetime.now()
        job.error = None
        storage.save_job(job)
    except Exception as exc:  # noqa: BLE001 — we want to swallow every failure here
        logger.exception("job %s failed", job_id)
        job.status = JobStatus.FAILED
        job.finished_at = datetime.now()
        job.error = str(exc)
        storage.save_job(job)


def _save_extracted_text(storage: LocalStorage, job_id: UUID, text: str) -> None:
    """Write the extracted plain text to ``extracted_text.txt``."""
    path = storage.job_dir(job_id) / "extracted_text.txt"
    path.write_text(text, encoding="utf-8")
    logger.debug("saved extracted_text.txt for job %s (%d chars)", job_id, len(text))


def _save_annotations(
    storage: LocalStorage,
    job_id: UUID,
    spans: list[EntitySpan],
    text: str,
) -> None:
    """Write detected spans as JSON for the feedback UI.

    Each annotation includes the entity text snippet, start/end offsets,
    entity type, and a ``source`` field (``"model"`` for neural model
    spans, ``"rule"`` for rule-based detector spans).
    """
    annotations = [
        {
            "start": span.start,
            "end": span.end,
            "entity_type": span.entity_type.value,
            "text": text[span.start : span.end],
            "source": _detect_source(span, text),
        }
        for span in spans
    ]
    path = storage.job_dir(job_id) / "annotations.json"
    path.write_text(json.dumps(annotations, ensure_ascii=False), encoding="utf-8")
    logger.debug("saved annotations.json for job %s (%d spans)", job_id, len(spans))


def _detect_source(span: EntitySpan, text: str) -> str:
    """Heuristic to determine whether a span came from the model or rules.

    This is a best-effort heuristic for the MVP. In production the
    ``CombinedPrivacyClient`` should tag spans with their origin.
    """
    # ACCOUNT_NUMBER spans that include a non-digit prefix (like "ИНН ")
    # are almost certainly from the rule detector.
    entity_text = text[span.start : span.end]
    if span.entity_type.value == "account_number" and not entity_text.strip().isdigit():
        return "rule"
    return "model"


def _converter_for(ext: str) -> DocumentConverter:
    """Look up the converter for ``ext`` or raise :class:`ValueError`."""
    try:
        return CONVERTERS[ext]
    except KeyError as exc:
        raise ValueError(f"no converter registered for extension {ext!r}") from exc


def _validate_conversion(source_ext: str, output_ext: str) -> None:
    """Reject unsupported conversion directions early.

    Currently only ``docx`` → ``md`` and identity conversions are
    supported.  ``md`` → ``docx`` would require building a real Word
    document from plain text (paragraphs, runs, tables) which is out of
    scope for the MVP.
    """
    if source_ext == output_ext:
        return
    if source_ext == "docx" and output_ext == "md":
        return
    raise ValueError(
        f"unsupported conversion {source_ext!r} → {output_ext!r}; "
        f"only docx→md, md→md and docx→docx are supported"
    )


def _docx_to_markdown(source_path: Path) -> str:
    """Convert a ``.docx`` file to markdown via pandoc.

    Pandoc is significantly better than python-docx at preserving the
    document structure (headings, lists, emphasis).  When pandoc is not
    available or fails, fall back to :class:`DocxConverter.extract_text`
    so the pipeline still produces a usable (if structurally-flat)
    text.  The returned string is what gets fed to the privacy-filter
    model and rewritten as ``result.md``.
    """
    import shutil
    import subprocess

    pandoc_exe: str | None = shutil.which("pandoc")
    if pandoc_exe is not None:
        try:
            completed = subprocess.run(
                [pandoc_exe, str(source_path), "-t", "markdown", "--wrap", "none"],
                capture_output=True,
                check=False,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            logger.warning("pandoc timed out on %s; falling back to python-docx", source_path)
            completed = None
        if completed is not None:
            if completed.returncode == 0:
                # Strip the trailing newline so offsets match the
                # python-docx path more closely.
                return completed.stdout.decode("utf-8").rstrip("\n")
            logger.warning(
                "pandoc exited %d on %s; falling back to python-docx: %s",
                completed.returncode,
                source_path,
                completed.stderr.decode("utf-8", errors="replace").strip(),
            )

    # Fallback: python-docx flat text via the docx converter.
    return DocxConverter().extract_text(source_path)


def _build_replacements(spans: list[EntitySpan]) -> list[Replacement]:
    """Allocate placeholder numbers in ascending order of span position."""
    ordered = sorted(spans, key=lambda span: span.start)
    counter = PlaceholderCounter()
    replacements: list[Replacement] = []
    for span in ordered:
        placeholder = counter.next(span.entity_type)
        replacements.append(
            Replacement(
                start=span.start,
                end=span.end,
                entity_type=span.entity_type,
                placeholder=placeholder,
            )
        )
    return replacements


__all__ = ["CONVERTERS", "run_job"]
