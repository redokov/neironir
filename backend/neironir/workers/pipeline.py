"""Background pipeline that drives a single :class:`Job` to completion.

The pipeline is a sequence of side-effecting steps orchestrated by
:func:`run_job`. It is intentionally linear: each step feeds the next,
and any error short-circuits the rest of the run and flips the job to
``FAILED``.
"""

from __future__ import annotations

import logging
from datetime import datetime
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
    """
    job = storage.load_job(job_id)
    try:
        job.status = JobStatus.PROCESSING
        storage.save_job(job)

        converter = _converter_for(job.source_ext)
        source_path = storage.job_dir(job_id) / f"source.{job.source_ext}"
        text = converter.extract_text(source_path)

        spans = await privacy.annotate(text)
        replacements = _build_replacements(spans)

        target_path = storage.job_dir(job_id) / f"result.{job.source_ext}"
        converter.build(source_path, target_path, replacements)

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


def _converter_for(ext: str) -> DocumentConverter:
    """Look up the converter for ``ext`` or raise :class:`ValueError`."""
    try:
        return CONVERTERS[ext]
    except KeyError as exc:
        raise ValueError(f"no converter registered for extension {ext!r}") from exc


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
