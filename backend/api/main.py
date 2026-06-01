from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.jobs.queue import JobQueue
from backend.pipeline.run import run_pipeline

app = FastAPI(title="Mapovač", description="Generátor OB map pro ČR (ISOM 2017)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API běží pod prefixem /api — stejně v dev (Vite proxy) i v produkci, kde
# FastAPI servíruje i sestavený frontend (viz mount na konci souboru).
router = APIRouter()

queue = JobQueue(max_workers=2)


class JobOptions(BaseModel):
    experimental_points: bool = Field(
        default=False,
        description="Detekce kamenů, kupek a výrazných stromů (experimentální).",
    )
    contour_interval: float = Field(default=5.0, gt=0, le=10)
    use_zabaged: bool = Field(default=False, description="Použít ZABAGED (vyžaduje účet ČÚZK).")
    download_laz: bool = Field(
        default=False,
        description="Stáhnout klasifikovaný point cloud (LAZ) — vyžaduje MAPOVAC_LAZ_URL_TEMPLATE.",
    )
    advanced: bool = Field(
        default=False,
        description=(
            "Pokročilé generování — zapojí další podklady (ortofoto + NDVI korekce "
            "vegetace, hustší DMPOK point cloud, katastr land-cover, DIBAVOD voda, "
            "RÚIAN budovy, detekce balvanů/stromů, magnetická deklinace, ortofoto "
            "jako template v .omap). Každý zdroj při chybě jen přeskočí."
        ),
    )


class JobRequest(BaseModel):
    # bbox v EPSG:4326 (WGS84): [west, south, east, north]
    bbox: tuple[float, float, float, float]
    scale: Literal[7500, 10000] = 10000
    options: JobOptions = Field(default_factory=JobOptions)


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.post("/jobs")
def create_job(req: JobRequest) -> dict:
    w, s, e, n = req.bbox
    if not (12 <= w < e <= 19 and 48 <= s < n <= 51.5):
        raise HTTPException(400, "bbox mimo ČR nebo neplatný")
    if (e - w) * (n - s) > 0.25:
        raise HTTPException(400, "Příliš velký výřez (limit ~25×25 km).")
    job = queue.submit(req.bbox, req.scale, req.options.model_dump(), run_pipeline)
    return job.to_dict()


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = queue.get(job_id)
    if not job:
        raise HTTPException(404, "job nenalezen")
    return job.to_dict()


def _serve(path: Path | None, media_type: str, filename: str) -> FileResponse:
    if not path or not path.exists():
        raise HTTPException(404, "soubor není připraven")
    return FileResponse(path, media_type=media_type, filename=filename)


@router.get("/jobs/{job_id}/pdf")
def get_pdf(job_id: str) -> FileResponse:
    job = queue.get(job_id)
    if not job:
        raise HTTPException(404)
    return _serve(job.pdf_path, "application/pdf", f"mapa-{job_id}.pdf")


@router.get("/jobs/{job_id}/omap")
def get_omap(job_id: str) -> FileResponse:
    job = queue.get(job_id)
    if not job:
        raise HTTPException(404)
    return _serve(job.omap_path, "application/xml", f"mapa-{job_id}.omap")


@router.get("/jobs/{job_id}/preview.png")
def get_preview(job_id: str) -> FileResponse:
    job = queue.get(job_id)
    if not job:
        raise HTTPException(404)
    return _serve(job.preview_path, "image/png", f"preview-{job_id}.png")


@router.get("/jobs/{job_id}/ortofoto.png")
def get_ortofoto(job_id: str) -> FileResponse:
    """Ortofoto podklad (pokročilé generování) — patří vedle .omap jako template."""
    job = queue.get(job_id)
    if not job or not job.preview_path:
        raise HTTPException(404)
    orto = job.preview_path.parent / "ortofoto.png"
    return _serve(orto, "image/png", "ortofoto.png")


# Všechny API routy pod /api (musí být PŘED mountem statiky na "/").
app.include_router(router, prefix="/api")

# Produkční režim: servíruj sestavený frontend (jediný proces, jeden port).
# `vite build` → frontend/dist. Když dist neexistuje (jedeme dev přes Vite),
# mount přeskočíme. Mount na "/" je catch-all, proto musí být POSLEDNÍ.
_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if (_FRONTEND_DIST / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="ui")
else:  # pragma: no cover
    import logging
    logging.getLogger("uvicorn.error").warning(
        "frontend/dist nenalezen — UI se neservíruje. Spusť `bash scripts/build-frontend.sh` "
        "(nebo používej dev režim `npm run dev`)."
    )
