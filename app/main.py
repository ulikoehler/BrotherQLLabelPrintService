import os
import uuid
import asyncio
import logging
import shutil
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .config import Config
from .converter import (
    convert_to_png,
    detect_file_type,
    is_accepted_filetype,
    get_original_dimensions_mm,
    get_pixel_dimensions,
    generate_preview,
    TAPE_PIXEL_MAP,
)
from .printer import (
    determine_orientation,
    prepare_image_for_print,
    do_print,
    get_printer_status,
    discover_printers,
    get_available_labels,
    get_available_models,
)
from .queue import PrintQueue, create_queue_item

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("brotherql")

config = Config("config.yaml")

app = FastAPI(
    title="Brother QL Print Service",
    description="Web service for printing labels on Brother QL series printers",
    version="1.0.0",
)

# Ensure data directories exist
data_dir = Path(config.storage["data_dir"])
uploads_dir = data_dir / "uploads"
prints_dir = data_dir / "prints"
previews_dir = data_dir / "previews"
for d in [uploads_dir, prints_dir, previews_dir]:
    d.mkdir(parents=True, exist_ok=True)

# Initialize queue
queue = PrintQueue(
    queue_file=config.storage["queue_file"],
    max_history=config.ui["max_history"],
)

# Queue processing state
_queue_processing = False


async def process_queue():
    """Background task to process pending print jobs sequentially."""
    global _queue_processing
    if _queue_processing:
        return
    _queue_processing = True

    try:
        while True:
            pending = queue.list_pending()
            queued_items = [item for item in pending if item.status == "queued"]
            if not queued_items:
                break

            item = queued_items[0]
            queue.update(item.id, status="printing")

            try:
                # Run print in a thread to not block the event loop
                print_config = config.printing
                printer_config = config.printer

                for copy_num in range(item.copies):
                    await asyncio.to_thread(
                        do_print,
                        image_path=item.stored_filename,
                        model=printer_config["model"],
                        backend=printer_config["backend"],
                        printer_identifier=printer_config["identifier"],
                        label=item.label or printer_config["label"],
                        rotate=str(item.rotation) if item.rotation else "auto",
                        threshold=float(print_config["threshold"]),
                        dither=print_config["dither"],
                        compress=print_config["compress"],
                        cut=print_config["cut"],
                        hq=print_config["hq"],
                        dpi_600=print_config["dpi_600"],
                    )

                queue.update(item.id, status="printed")
                logger.info(f"Printed: {item.original_filename} (id={item.id})")

            except Exception as e:
                logger.error(f"Print failed for {item.id}: {e}")
                queue.update(item.id, status="failed", error_message=str(e))
    finally:
        _queue_processing = False


@app.on_event("startup")
async def startup_event():
    logger.info(
        f"Brother QL Print Service starting — "
        f"printer: {config.printer['model']} at {config.printer['identifier']}"
    )


# --- Pydantic models ---

class PrintRequest(BaseModel):
    file_id: str
    label: Optional[str] = None
    orientation: Optional[str] = None
    resize: bool = False
    copies: int = 1
    rotate: Optional[str] = None


class PreviewRequest(BaseModel):
    file_id: str
    orientation: Optional[str] = None
    resize: bool = False
    label: Optional[str] = None


class SettingsUpdate(BaseModel):
    printer: Optional[dict] = None
    printing: Optional[dict] = None
    ui: Optional[dict] = None


# --- API Endpoints ---

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a file (image, PDF, or SVG) for printing."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    if not is_accepted_filetype(file.filename):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Accepted: images (png, jpg, gif, bmp, webp, tiff), PDF, SVG",
        )

    file_id = str(uuid.uuid4())
    ext = Path(file.filename).suffix
    stored_name = f"{file_id}{ext}"
    stored_path = uploads_dir / stored_name

    with open(stored_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    file_type = detect_file_type(file.filename)
    tape_width = config.printing["tape_width_mm"]

    # Get original dimensions in mm from source file metadata
    try:
        w_mm, h_mm = get_original_dimensions_mm(str(stored_path))
    except Exception as e:
        os.remove(stored_path)
        raise HTTPException(status_code=422, detail=f"Could not determine dimensions: {str(e)}")

    # Auto-detect orientation from original dimensions
    orient_result = determine_orientation(
        width_mm=w_mm,
        height_mm=h_mm,
        tape_width_mm=tape_width,
    )

    # Convert to PNG with appropriate orientation
    convert_dir = str(prints_dir)
    try:
        png_paths = convert_to_png(
            str(stored_path), convert_dir, tape_width,
            orientation=orient_result.orientation,
        )
    except Exception as e:
        os.remove(stored_path)
        raise HTTPException(status_code=422, detail=f"Conversion failed: {str(e)}")

    # Get pixel dimensions from first converted PNG
    first_png = png_paths[0]
    w_px, h_px = get_pixel_dimensions(first_png)

    return {
        "file_id": file_id,
        "original_filename": file.filename,
        "file_type": file_type,
        "stored_path": str(stored_path),
        "png_paths": png_paths,
        "dimensions_mm": {"width": w_mm, "height": h_mm},
        "dimensions_px": {"width": w_px, "height": h_px},
        "orientation": {
            "accepted": orient_result.accepted,
            "orientation": orient_result.orientation,
            "rotation": orient_result.rotation,
            "needs_resize": orient_result.needs_resize,
            "reason": orient_result.reason,
        },
        "num_pages": len(png_paths),
    }


@app.post("/api/preview")
async def create_preview(req: PreviewRequest):
    """Generate a preview PNG for the given uploaded file."""
    # Find the uploaded file
    upload_files = list(uploads_dir.glob(f"{req.file_id}.*"))
    if not upload_files:
        raise HTTPException(status_code=404, detail="File not found")

    upload_path = upload_files[0]
    tape_width = config.printing["tape_width_mm"]

    # Get original dimensions in mm from source file metadata
    try:
        w_mm, h_mm = get_original_dimensions_mm(str(upload_path))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not determine dimensions: {str(e)}")

    # Determine orientation from original dimensions
    orient_result = determine_orientation(
        width_mm=w_mm,
        height_mm=h_mm,
        tape_width_mm=tape_width,
        requested_orientation=req.orientation,
        resize=req.resize,
    )

    if not orient_result.accepted:
        raise HTTPException(
            status_code=422,
            detail=f"Orientation cannot be determined: {orient_result.reason}",
        )

    # Convert to PNG with appropriate orientation
    png_dir = str(prints_dir)
    try:
        png_paths = convert_to_png(
            str(upload_path), png_dir, tape_width,
            orientation=orient_result.orientation,
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Conversion failed: {str(e)}")

    first_png = png_paths[0]

    # Prepare image (resize/rotate)
    preview_id = str(uuid.uuid4())
    prepared_path = str(prints_dir / f"prepared_{preview_id}.png")
    prepare_image_for_print(
        png_path=first_png,
        output_path=prepared_path,
        tape_width_mm=tape_width,
        orientation=orient_result.orientation or "portrait",
        needs_resize=orient_result.needs_resize,
    )

    # Generate preview
    preview_path = str(previews_dir / f"{preview_id}.png")
    generate_preview(prepared_path, preview_path, tape_width)

    return {
        "preview_id": preview_id,
        "preview_url": f"/api/preview/{preview_id}",
        "orientation": orient_result.orientation,
        "rotation": orient_result.rotation,
        "needs_resize": orient_result.needs_resize,
        "reason": orient_result.reason,
        "prepared_path": prepared_path,
        "dimensions_mm": {"width": w_mm, "height": h_mm},
    }


@app.get("/api/preview/{preview_id}")
async def get_preview(preview_id: str):
    """Get a preview PNG by ID."""
    preview_path = previews_dir / f"{preview_id}.png"
    if not preview_path.exists():
        raise HTTPException(status_code=404, detail="Preview not found")
    return FileResponse(str(preview_path), media_type="image/png")


@app.post("/api/print")
async def print_file(req: PrintRequest):
    """Add a file to the print queue."""
    # Find the uploaded file
    upload_files = list(uploads_dir.glob(f"{req.file_id}.*"))
    if not upload_files:
        raise HTTPException(status_code=404, detail="File not found")

    upload_path = upload_files[0]
    tape_width = config.printing["tape_width_mm"]
    label = req.label or config.printer["label"]

    # Get original dimensions in mm from source file metadata
    try:
        w_mm, h_mm = get_original_dimensions_mm(str(upload_path))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not determine dimensions: {str(e)}")

    # Determine orientation from original dimensions
    orient_result = determine_orientation(
        width_mm=w_mm,
        height_mm=h_mm,
        tape_width_mm=tape_width,
        requested_orientation=req.orientation,
        resize=req.resize,
    )

    if not orient_result.accepted:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot print: {orient_result.reason}",
        )

    # Convert to PNG with appropriate orientation
    png_dir = str(prints_dir)
    try:
        png_paths = convert_to_png(
            str(upload_path), png_dir, tape_width,
            orientation=orient_result.orientation,
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Conversion failed: {str(e)}")

    first_png = png_paths[0]

    # Prepare image
    print_id = str(uuid.uuid4())
    prepared_path = str(prints_dir / f"print_{print_id}.png")
    prepare_image_for_print(
        png_path=first_png,
        output_path=prepared_path,
        tape_width_mm=tape_width,
        orientation=orient_result.orientation or "portrait",
        needs_resize=orient_result.needs_resize,
    )

    # Generate preview if enabled
    preview_filename = ""
    if config.ui["show_preview"]:
        preview_filename = str(previews_dir / f"{print_id}.png")
        generate_preview(prepared_path, preview_filename, tape_width)
        preview_filename = f"{print_id}.png"

    # Get original filename
    original_name = upload_path.name

    # Create queue item
    item = create_queue_item(
        original_filename=original_name,
        stored_filename=prepared_path,
        label=label,
        rotation=orient_result.rotation,
        copies=req.copies,
        orientation=orient_result.orientation or "portrait",
        width_mm=w_mm,
        height_mm=h_mm,
        preview_filename=preview_filename,
    )
    queue.add(item)

    # Trigger queue processing
    asyncio.create_task(process_queue())

    return item.to_dict()


@app.get("/api/queue")
async def get_queue():
    """Get print history (last N items)."""
    items = queue.list_all()
    return [item.to_dict() for item in items]


@app.delete("/api/queue/{item_id}")
async def remove_queue_item(item_id: str):
    """Remove/cancel a queue item."""
    item = queue.get(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    queue.remove(item_id)
    return {"status": "removed", "id": item_id}


@app.get("/api/settings")
async def get_settings():
    """Get current settings."""
    return config.to_dict()


@app.put("/api/settings")
async def update_settings(update: SettingsUpdate):
    """Update settings."""
    partial = update.model_dump(exclude_none=True)
    if not partial:
        raise HTTPException(status_code=400, detail="No settings provided")
    config.update(partial)
    # Update queue max_history if changed
    if "ui" in partial and "max_history" in partial.get("ui", {}):
        queue.max_history = partial["ui"]["max_history"]
    return config.to_dict()


@app.get("/api/printer/status")
async def printer_status():
    """Query printer status."""
    try:
        status = await asyncio.to_thread(
            get_printer_status,
            model=config.printer["model"],
            backend=config.printer["backend"],
            printer_identifier=config.printer["identifier"],
        )
        return {"status": str(status)}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/labels")
async def list_labels():
    """List available label sizes."""
    try:
        return await asyncio.to_thread(get_available_labels)
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/models")
async def list_models():
    """List supported printer models."""
    try:
        return await asyncio.to_thread(get_available_models)
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/discover")
async def discover():
    """Discover connected printers."""
    try:
        devices = await asyncio.to_thread(
            discover_printers, config.printer["backend"]
        )
        return {"devices": [str(d) for d in devices]}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/files/{filename}")
async def get_file(filename: str):
    """Serve a file from the previews directory."""
    # Check previews first
    preview_path = previews_dir / filename
    if preview_path.exists():
        return FileResponse(str(preview_path))

    # Check prints
    print_path = prints_dir / filename
    if print_path.exists():
        return FileResponse(str(print_path))

    raise HTTPException(status_code=404, detail="File not found")


# --- Static UI ---

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(static_dir / "index.html"))


def main():
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=config.server["host"],
        port=config.server["port"],
        workers=config.server["workers"],
    )


if __name__ == "__main__":
    main()
