import os
import re
import subprocess
from pathlib import Path
from PIL import Image
from typing import Optional, Tuple

# Tape width (mm) → printable pixel width at 300dpi
# From brother_ql info labels
TAPE_PIXEL_MAP = {
    12: 106,
    29: 306,
    38: 413,
    50: 554,
    54: 590,
    62: 696,
    102: 1164,
    103: 1200,
}

# DPI assumption for images without embedded DPI metadata
DEFAULT_IMAGE_DPI = 300

# Accepted file extensions
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".tif"}
PDF_EXTS = {".pdf"}
SVG_EXTS = {".svg"}


def detect_file_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in SVG_EXTS:
        return "svg"
    return "unknown"


def is_accepted_filetype(filename: str) -> bool:
    return detect_file_type(filename) != "unknown"


def _convert_pdf(input_path: str, output_dir: str, scale_to: int = 696, scale_axis: str = "y") -> list[str]:
    """Convert PDF to PNG(s) using pdftoppm. Returns list of PNG paths."""
    base = Path(input_path).stem
    output_prefix = os.path.join(output_dir, base)
    flag = "-scale-to-y" if scale_axis == "y" else "-scale-to-x"
    cmd = [
        "pdftoppm", "-png", flag, str(scale_to),
        input_path, output_prefix,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"pdftoppm failed: {result.stderr}")
    pngs = sorted(Path(output_dir).glob(f"{base}*.png"))
    if not pngs:
        raise RuntimeError("pdftoppm produced no output files")
    return [str(p) for p in pngs]


def _convert_svg(input_path: str, output_path: str, width_px: int = 696) -> str:
    """Convert SVG to PNG using rsvg-convert."""
    cmd = [
        "rsvg-convert", "-w", str(width_px), "-f", "png",
        "-o", output_path, input_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"rsvg-convert failed: {result.stderr}")
    return output_path


def _convert_image(input_path: str, output_path: str) -> str:
    """Convert/normalize an image to PNG using Pillow."""
    img = Image.open(input_path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.save(output_path, "PNG")
    return output_path


def convert_to_png(
    input_path: str,
    output_dir: str,
    tape_width_mm: int = 62,
    orientation: Optional[str] = None,
) -> list[str]:
    """
    Convert any supported file to PNG(s).
    Returns list of paths to generated PNG files.

    For PDFs:
      - portrait (width=tape): use --scale-to-x to scale width to pixel_width
      - landscape (height=tape): use --scale-to-y to scale height to pixel_width
      - unknown: use --scale-to-y as default (user's original workflow)
    For SVGs: scale width to pixel_width.
    For images: convert to PNG as-is.
    """
    pixel_width = TAPE_PIXEL_MAP.get(tape_width_mm, 696)
    file_type = detect_file_type(input_path)

    if file_type == "pdf":
        if orientation == "portrait":
            return _convert_pdf(input_path, output_dir, scale_to=pixel_width, scale_axis="x")
        else:
            return _convert_pdf(input_path, output_dir, scale_to=pixel_width, scale_axis="y")
    elif file_type == "svg":
        out = os.path.join(output_dir, Path(input_path).stem + ".png")
        return [_convert_svg(input_path, out, width_px=pixel_width)]
    elif file_type == "image":
        out = os.path.join(output_dir, Path(input_path).stem + ".png")
        return [_convert_image(input_path, out)]
    else:
        raise ValueError(f"Unsupported file type: {input_path}")


def get_pdf_dimensions_mm(pdf_path: str) -> Tuple[float, float]:
    """
    Get the page dimensions of a PDF in millimeters using pdfinfo.
    Returns (width_mm, height_mm) of the first page.
    """
    result = subprocess.run(
        ["pdfinfo", "-f", "1", "-l", "1", pdf_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pdfinfo failed: {result.stderr}")

    # Parse 'Page size: 175.4 x 62.2 pts' or similar
    match = re.search(r"Page size:\s+([\d.]+)\s+x\s+([\d.]+)\s+pts", result.stdout)
    if not match:
        match = re.search(r"Page size:\s+([\d.]+)\s+x\s+([\d.]+)", result.stdout)
    if not match:
        raise RuntimeError("Could not parse page size from pdfinfo output")

    w_pts = float(match.group(1))
    h_pts = float(match.group(2))
    # 1 point = 1/72 inch = 25.4/72 mm
    w_mm = w_pts * 25.4 / 72.0
    h_mm = h_pts * 25.4 / 72.0
    return (round(w_mm, 1), round(h_mm, 1))


def get_svg_dimensions_mm(svg_path: str) -> Tuple[float, float]:
    """
    Try to extract dimensions from an SVG file.
    Parses width/height attributes or viewBox.
    Returns (width_mm, height_mm).
    """
    with open(svg_path, "r") as f:
        content = f.read(4096)

    # Try width/height attributes with mm units
    w_match = re.search(r'\bwidth\s*=\s*["\']([\d.]+)mm["\']', content)
    h_match = re.search(r'\bheight\s*=\s*["\']([\d.]+)mm["\']', content)
    if w_match and h_match:
        return (float(w_match.group(1)), float(h_match.group(1)))

    # Try width/height with px units (assume 300dpi)
    w_match = re.search(r'\bwidth\s*=\s*["\']([\d.]+)px["\']', content)
    h_match = re.search(r'\bheight\s*=\s*["\']([\d.]+)px["\']', content)
    if w_match and h_match:
        w_mm = float(w_match.group(1)) / DEFAULT_IMAGE_DPI * 25.4
        h_mm = float(h_match.group(1)) / DEFAULT_IMAGE_DPI * 25.4
        return (round(w_mm, 1), round(h_mm, 1))

    # Try viewBox
    vb_match = re.search(r'viewBox\s*=\s*["\']\s*[\d.]+\s+[\d.]+\s+([\d.]+)\s+([\d.]+)["\']', content)
    if vb_match:
        w_mm = float(vb_match.group(1)) / DEFAULT_IMAGE_DPI * 25.4
        h_mm = float(vb_match.group(2)) / DEFAULT_IMAGE_DPI * 25.4
        return (round(w_mm, 1), round(h_mm, 1))

    raise RuntimeError("Could not determine SVG dimensions")


def get_original_dimensions_mm(file_path: str) -> Tuple[float, float]:
    """
    Get the original document dimensions in mm from the source file.
    - PDF: uses pdfinfo to read page size
    - SVG: parses width/height or viewBox
    - Image: uses embedded DPI metadata
    Returns (width_mm, height_mm).
    """
    file_type = detect_file_type(file_path)

    if file_type == "pdf":
        return get_pdf_dimensions_mm(file_path)
    elif file_type == "svg":
        return get_svg_dimensions_mm(file_path)
    elif file_type == "image":
        img = Image.open(file_path)
        w_px, h_px = img.size
        dpi = img.info.get("dpi", (DEFAULT_IMAGE_DPI, DEFAULT_IMAGE_DPI))
        dpi_x = float(dpi[0]) if dpi[0] and dpi[0] > 0 else DEFAULT_IMAGE_DPI
        dpi_y = float(dpi[1]) if dpi[1] and dpi[1] > 0 else DEFAULT_IMAGE_DPI
        w_mm = w_px / dpi_x * 25.4
        h_mm = h_px / dpi_y * 25.4
        return (round(w_mm, 1), round(h_mm, 1))
    else:
        raise ValueError(f"Unsupported file type: {file_path}")


def get_dimensions_mm(
    png_path: str,
    tape_width_mm: int = 62,
) -> tuple[float, float]:
    """
    Determine the dimensions of a PNG in millimeters.
    Uses embedded DPI if available, otherwise assumes 300 DPI.
    Returns (width_mm, height_mm).
    """
    img = Image.open(png_path)
    w_px, h_px = img.size
    dpi = img.info.get("dpi", (DEFAULT_IMAGE_DPI, DEFAULT_IMAGE_DPI))
    dpi_x = float(dpi[0]) if dpi[0] and dpi[0] > 0 else DEFAULT_IMAGE_DPI
    dpi_y = float(dpi[1]) if dpi[1] and dpi[1] > 0 else DEFAULT_IMAGE_DPI

    w_mm = w_px / dpi_x * 25.4
    h_mm = h_px / dpi_y * 25.4

    return (round(w_mm, 1), round(h_mm, 1))


def get_pixel_dimensions(png_path: str) -> tuple[int, int]:
    """Return pixel dimensions (width, height) of a PNG."""
    img = Image.open(png_path)
    return img.size


def resize_for_print(
    png_path: str,
    output_path: str,
    tape_width_mm: int = 62,
    orientation: str = "portrait",
) -> str:
    """
    Resize an image to fit the tape width.
    - portrait: scale width to tape pixel width, maintain aspect ratio
    - landscape: scale height to tape pixel width, then rotate 90°
    """
    pixel_width = TAPE_PIXEL_MAP.get(tape_width_mm, 696)
    img = Image.open(png_path)
    if img.mode != "RGB":
        img = img.convert("RGB")

    if orientation == "landscape":
        # Scale height to pixel_width, maintain aspect ratio
        w_px, h_px = img.size
        new_h = pixel_width
        new_w = int(w_px * (new_h / h_px))
        img = img.resize((new_w, new_h), Image.LANCZOS)
        img = img.rotate(90, expand=True)
    else:
        # Portrait: scale width to pixel_width
        w_px, h_px = img.size
        new_w = pixel_width
        new_h = int(h_px * (new_w / w_px))
        img = img.resize((new_w, new_h), Image.LANCZOS)

    img.save(output_path, "PNG")
    return output_path


def generate_preview(
    png_path: str,
    output_path: str,
    tape_width_mm: int = 62,
    max_preview_width: int = 400,
) -> str:
    """Generate a smaller preview PNG for the UI."""
    img = Image.open(png_path)
    if img.mode != "RGB":
        img = img.convert("RGB")

    w, h = img.size
    scale = min(max_preview_width / w, max_preview_width / h)
    if scale < 1.0:
        new_w = int(w * scale)
        new_h = int(h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)

    img.save(output_path, "PNG")
    return output_path
