"""Admin module: statistics, training trigger, and admin HTTP API."""

from neironir.admin.router import router
from neironir.admin.stats import compute_documents_stats, compute_jobs_with_feedback
from neironir.admin.training import (
    TrainingState,
    TrainingStatus,
    get_training_state,
    reset_training_state,
    start_training,
    stop_training,
)

__all__ = [
    "compute_documents_stats",
    "compute_jobs_with_feedback",
    "TrainingState",
    "TrainingStatus",
    "get_training_state",
    "reset_training_state",
    "start_training",
    "stop_training",
    "router",
]