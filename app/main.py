from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .agent import FacilityInspectionAgent
from .config import settings
from .image_utils import InvalidImageError
from .providers import ProviderError
from .schemas import InspectionResult


app = FastAPI(
    title="SEEFIX Facility Inspection Agent POC",
    version="0.1.0",
    description="Standalone multimodal facility-image assessment prototype.",
)
agent = FacilityInspectionAgent(settings)
STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "runtime": settings.runtime,
        "provider": agent.provider.name,
        "model": agent.provider.model_id,
        "max_image_dimension": settings.max_image_dimension,
        "model_context": settings.model_context,
        "model_max_output_tokens": settings.model_max_output_tokens,
    }


@app.post("/api/analyze", response_model=InspectionResult)
async def analyze_image(
    image: UploadFile = File(...),
) -> InspectionResult:
    image_bytes = await image.read()
    try:
        return agent.analyze(image_bytes)
    except InvalidImageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
