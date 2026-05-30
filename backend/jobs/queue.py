from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

JobStatus = Literal["pending", "running", "done", "error"]


@dataclass
class Job:
    id: str
    bbox: tuple[float, float, float, float]
    scale: int
    options: dict
    status: JobStatus = "pending"
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    pdf_path: Path | None = None
    omap_path: Path | None = None
    preview_path: Path | None = None
    _future: Future | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "bbox": self.bbox,
            "scale": self.scale,
            "options": self.options,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "has_pdf": self.pdf_path is not None and self.pdf_path.exists(),
            "has_omap": self.omap_path is not None and self.omap_path.exists(),
            "has_preview": self.preview_path is not None and self.preview_path.exists(),
        }


class JobQueue:
    def __init__(self, max_workers: int = 2) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(
        self,
        bbox: tuple[float, float, float, float],
        scale: int,
        options: dict,
        runner: Callable[[Job], None],
    ) -> Job:
        job = Job(id=uuid.uuid4().hex, bbox=bbox, scale=scale, options=options)
        with self._lock:
            self._jobs[job.id] = job

        def _run() -> None:
            try:
                job.status = "running"
                runner(job)
                job.status = "done"
                job.progress = 1.0
            except Exception as exc:
                job.status = "error"
                job.error = repr(exc)

        job._future = self._executor.submit(_run)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)
