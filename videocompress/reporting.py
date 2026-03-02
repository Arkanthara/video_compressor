"""JSON report generation.

Creates a timestamped JSON file containing the full
:meth:`~videocompress.models.JobOutcome.to_dict` payload, suitable for
automation, auditing, and downstream analysis.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from videocompress.models import JobOutcome


def write_report(path: Path, outcome: JobOutcome) -> None:
    """Write a JSON report for *outcome* to *path*.

    Parent directories are created automatically if they do not exist.
    """
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "job": outcome.to_dict(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
