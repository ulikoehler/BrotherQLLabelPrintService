import logging
from typing import Optional
from PIL import Image

from .converter import TAPE_PIXEL_MAP, get_dimensions_mm, resize_for_print

logger = logging.getLogger("brotherql.printer")


class OrientationResult:
    def __init__(
        self,
        accepted: bool,
        orientation: Optional[str] = None,
        rotation: int = 0,
        needs_resize: bool = False,
        reason: str = "",
    ):
        self.accepted = accepted
        self.orientation = orientation
        self.rotation = rotation
        self.needs_resize = needs_resize
        self.reason = reason


def determine_orientation(
    width_mm: float,
    height_mm: float,
    tape_width_mm: int = 62,
    requested_orientation: Optional[str] = None,
    resize: bool = False,
) -> OrientationResult:
    """
    Determine print orientation based on document dimensions and tape width.

    Rules:
    1. If width ≈ tape_width → portrait, no rotation needed
    2. If height ≈ tape_width → landscape, rotate 90°
    3. If neither matches:
       - Refuse unless requested_orientation is given AND resize=true
       - If orientation=portrait: scale width to tape width
       - If orientation=landscape: scale height to tape width, rotate 90°
    """
    tolerance = 1.0  # mm tolerance for matching

    width_matches = abs(width_mm - tape_width_mm) < tolerance
    height_matches = abs(height_mm - tape_width_mm) < tolerance

    if width_matches and not height_matches:
        return OrientationResult(
            accepted=True,
            orientation="portrait",
            rotation=0,
            needs_resize=False,
            reason="Width matches tape width — portrait orientation detected.",
        )

    if height_matches and not width_matches:
        return OrientationResult(
            accepted=True,
            orientation="landscape",
            rotation=90,
            needs_resize=False,
            reason="Height matches tape width — landscape orientation detected (rotate 90°).",
        )

    if width_matches and height_matches:
        return OrientationResult(
            accepted=True,
            orientation="portrait",
            rotation=0,
            needs_resize=False,
            reason="Both dimensions match tape width — using portrait.",
        )

    # Neither dimension matches
    if requested_orientation is not None and resize:
        if requested_orientation == "portrait":
            return OrientationResult(
                accepted=True,
                orientation="portrait",
                rotation=0,
                needs_resize=True,
                reason="Neither dimension matches tape width. Resizing to portrait as requested.",
            )
        elif requested_orientation == "landscape":
            return OrientationResult(
                accepted=True,
                orientation="landscape",
                rotation=90,
                needs_resize=True,
                reason="Neither dimension matches tape width. Resizing to landscape as requested (rotate 90°).",
            )

    return OrientationResult(
        accepted=False,
        orientation=None,
        rotation=0,
        needs_resize=False,
        reason=(
            f"Neither dimension ({width_mm}x{height_mm}mm) matches tape width "
            f"({tape_width_mm}mm). Provide an explicit orientation and set resize=true."
        ),
    )


def prepare_image_for_print(
    png_path: str,
    output_path: str,
    tape_width_mm: int = 62,
    orientation: str = "portrait",
    needs_resize: bool = False,
) -> str:
    """
    Prepare the PNG for printing: resize if needed and rotate if landscape.
    Returns the path to the prepared image.
    """
    if needs_resize:
        return resize_for_print(
            png_path, output_path, tape_width_mm, orientation
        )

    # Even without resize, we may need to rotate for landscape
    if orientation == "landscape":
        img = Image.open(png_path)
        if img.mode != "RGB":
            img = img.convert("RGB")
        img = img.rotate(90, expand=True)
        img.save(output_path, "PNG")
        return output_path

    # Portrait, no resize — just copy/convert
    img = Image.open(png_path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.save(output_path, "PNG")
    return output_path


def do_print(
    image_path: str,
    model: str,
    backend: str,
    printer_identifier: str,
    label: str,
    rotate: str = "auto",
    threshold: float = 70.0,
    dither: bool = False,
    compress: bool = False,
    cut: bool = True,
    hq: bool = True,
    dpi_600: bool = False,
    red: bool = False,
) -> None:
    """
    Print an image using the brother_ql_next Python API.
    """
    from brother_ql.conversion import convert
    from brother_ql.backends.helpers import send
    from brother_ql.raster import BrotherQLRaster

    qlr = BrotherQLRaster(model)
    qlr.exception_on_warning = True

    instructions = convert(
        qlr=qlr,
        images=[image_path],
        label=label,
        rotate=rotate,
        threshold=threshold,
        dither=dither,
        compress=compress,
        cut=cut,
        hq=hq,
        dpi_600=dpi_600,
        red=red,
    )

    send(
        instructions=instructions,
        printer_identifier=printer_identifier,
        backend_identifier=backend,
        blocking=True,
    )
    logger.info(f"Printed {image_path} on {model} via {backend}")


def get_printer_status(
    model: str,
    backend: str,
    printer_identifier: str,
) -> dict:
    """Query printer status via brother_ql_next."""
    from brother_ql.backends.helpers import status as status_fn

    status, raw = status_fn(
        printer_model=model,
        printer_identifier=printer_identifier,
        backend_identifier=backend,
    )
    return status


def discover_printers(backend: str = "pyusb") -> list:
    """Discover connected printers."""
    from brother_ql.backends.helpers import discover

    devices = discover(backend_identifier=backend)
    return devices


def get_available_labels() -> list[dict]:
    """Return list of available label types."""
    from brother_ql.labels import LabelsManager

    manager = LabelsManager()
    labels = []
    for identifier in manager.identifiers():
        label = manager.get(identifier)
        if label:
            labels.append({
                "identifier": identifier,
                "name": label.name,
                "tape_size_mm": label.tape_size,
                "printable_px": label.dots_printable,
                "total_px": label.dots_total,
                "form_factor": str(label.form_factor),
                "color": str(label.color),
            })
    return labels


def get_available_models() -> list[str]:
    """Return list of supported printer models."""
    from brother_ql.models import ModelsManager

    return list(ModelsManager().identifiers())
