
import json
import logging
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from .splitter import collect_resources_except_bundle, group_by_resource_type, ui_mapping, build_zip_bytes
from .database import save_grouped_resources

project_root = Path(__file__).resolve().parents[1]
load_dotenv(project_root / ".env.local")
load_dotenv(project_root / ".env")

app = FastAPI()
logger = logging.getLogger(__name__)


async def _group_uploaded_files(files: list[UploadFile]):
    if not files:
        raise HTTPException(status_code=400, detail="Upload at least one JSON file.")

    grouped = {}
    for uploaded_file in files:
        try:
            data = json.loads((await uploaded_file.read()).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"{uploaded_file.filename or 'Uploaded file'} is not valid UTF-8 JSON.",
            ) from exc

        file_grouped = group_by_resource_type(collect_resources_except_bundle(data, []))
        for resource_type, resource_data in file_grouped.items():
            target = grouped.setdefault(resource_type, {"parent": [], "children": {}})
            target["parent"].extend(resource_data.get("parent") or [])
            for child_key, rows in (resource_data.get("children") or {}).items():
                target["children"].setdefault(child_key, []).extend(rows)

    return grouped

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse((Path(__file__).parent/"static"/"index.html").read_text(encoding="utf-8"))

@app.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    data = json.loads((await file.read()).decode("utf-8"))
    grouped = group_by_resource_type(collect_resources_except_bundle(data, []))
    return JSONResponse(ui_mapping(grouped))

@app.post("/download")
async def download(file: UploadFile = File(...)):
    data = json.loads((await file.read()).decode("utf-8"))
    grouped = group_by_resource_type(collect_resources_except_bundle(data, []))
    blob = build_zip_bytes(grouped)
    return StreamingResponse(iter([blob]), media_type="application/zip",
                             headers={"Content-Disposition": "attachment; filename=Output.zip"})


@app.post("/analyze-batch")
async def analyze_batch(files: list[UploadFile] = File(...)):
    grouped = await _group_uploaded_files(files)
    return JSONResponse(ui_mapping(grouped))


@app.post("/download-batch")
async def download_batch(files: list[UploadFile] = File(...)):
    grouped = await _group_uploaded_files(files)
    blob = build_zip_bytes(grouped)
    return StreamingResponse(iter([blob]), media_type="application/zip",
                             headers={"Content-Disposition": "attachment; filename=Output.zip"})


@app.post("/save-to-mysql")
async def save_to_mysql(files: list[UploadFile] = File(...)):
    grouped = await _group_uploaded_files(files)
    try:
        result = save_grouped_resources(grouped)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Could not save uploaded FHIR resources to the database")
        raise HTTPException(
            status_code=503,
            detail="Database save failed. Check DATABASE_URL, MySQL availability, and database permissions.",
        ) from exc

    return JSONResponse({"files_processed": len(files), **result})
