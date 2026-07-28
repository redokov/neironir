"""Job aggregate: a single document being processed by the pipeline.

The :class:`Job` is the durable, serialisable representation of a redaction
request. It is written to and read from ``storage/jobs/{id}/job.json`` by
`storage/local.py` (phase 3) using :meth:`to_dict` and :meth:`from_dict`, so
the on-disk shape is plain JSON — no Python-specific types survive a
serialise/deserialize round trip.

The class is a Pydantic v2 ``BaseModel`` (rather than a ``@dataclass``) so
that ``datetime`` fields round-trip through JSON as ISO 8601 strings
automatically, and so that :class:`neironir.api.schemas.JobResponse` can be
built from a ``Job`` instance via ``model_validate(job)`` with
``from_attributes=True`` (no manual mapper needed in phase 3).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(str, Enum):  # noqa: UP042
    """Lifecycle states of a :class:`Job`.

    String values are the lower-case API form, matching ``docs/api.md``.

    The spec (``docs/agents/02-domain-and-contracts.md``) prescribes the
    ``(str, Enum)`` form; we keep it verbatim even though ``enum.StrEnum``
    would be slightly cleaner on Python 3.11+.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Job(BaseModel):
    """A redaction request and its current state."""

    model_config = ConfigDict(use_enum_values=False, validate_assignment=True)

    id: UUID = Field(default_factory=uuid4)
    status: JobStatus = JobStatus.PENDING
    source_filename: str
    source_ext: Literal["md", "docx"]
    # Format the cleaned file is written in.  Defaults to ``source_ext``
    # but a user can ask the server to convert a ``.docx`` to ``.md`` via
    # the ``output_format`` flag on the upload endpoint.
    output_ext: Literal["md", "docx"] | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    finished_at: datetime | None = None
    error: str | None = None

    @property
    def effective_output_ext(self) -> Literal["md", "docx"]:
        """Return ``output_ext`` if set, otherwise ``source_ext``."""
        return self.output_ext if self.output_ext is not None else self.source_ext

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain ``dict`` ready for ``json.dump``."""
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Job:
        """Rehydrate a :class:`Job` from its JSON-compatible ``dict`` form."""
        return cls.model_validate(data)


__all__ = ["Job", "JobStatus"]
