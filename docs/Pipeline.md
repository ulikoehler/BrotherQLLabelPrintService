# Processing Pipeline

This document describes the complete processing pipeline from file upload to printed label, including all intermediate steps, conditional branches, and configuration options.

## Overview

```
Upload → Dimension Detection → Orientation Detection → Conversion to PNG →
  Preview (optional) → Print Queue → Queue Processing → Printer
```

Each step is detailed below.

---

## 1. File Upload (`POST /api/upload`)

**Entry point**: `app/main.py` → `upload_file()`

1. Validate filename is present and extension is accepted (PNG, JPG, JPEG, GIF, BMP, WebP, TIFF, PDF, SVG).
2. Generate a UUID `file_id` and save the uploaded file to `{data_dir}/uploads/{file_id}.{ext}`.
3. Proceed to **Step 2: Dimension Detection**.

**On error**: File is deleted from disk, HTTP 400 (unsupported type) or HTTP 422 (dimension/conversion failure).

---

## 2. Dimension Detection

**Module**: `app/converter.py` → `get_original_dimensions_mm()`

Extracts the original document dimensions in millimeters from the source file metadata:

- **PDF**: Runs `pdfinfo -f 1 -l 1 {file}` and parses the `Page size` line. Supports `pts`, `mm`, and `in` units. Returns dimensions of the first page.
- **SVG**: Parses `width`/`height` attributes (mm or px units, px assumed at 300 DPI) or falls back to `viewBox` dimensions.
- **Image (PNG/JPG/etc.)**: Uses Pillow to read pixel dimensions and embedded DPI metadata. Falls back to 300 DPI if no DPI metadata is present.

**Output**: `(width_mm, height_mm)` — rounded to 1 decimal place.

**On error**: File is deleted, HTTP 422 with detail `"Could not determine dimensions: {error}"`.

---

## 3. Orientation Detection

**Module**: `app/printer.py` → `determine_orientation()`

Determines how the document should be oriented on the tape. Uses a 1.0mm tolerance for matching.

### Rules (evaluated in order):

1. **Width matches tape width** (within tolerance):
   - Orientation: `portrait`
   - Rotation: `0°`
   - Needs resize: `No`
   - Reason: "Width matches tape width — portrait orientation detected."

2. **Height matches tape width** (within tolerance):
   - Orientation: `landscape`
   - Rotation: `90°`
   - Needs resize: `No`
   - Reason: "Height matches tape width — landscape orientation detected (rotate 90°)."

3. **Both dimensions match tape width** (square):
   - Orientation: `portrait`
   - Rotation: `0°`
   - Needs resize: `No`
   - Reason: "Both dimensions match tape width — using portrait."

4. **Neither dimension matches tape width**:
   - **If `orientation` is explicitly provided AND `resize=true`**:
     - Portrait: scale width to tape pixel width, maintain aspect ratio.
     - Landscape: scale height to tape pixel width, then rotate 90°.
     - Needs resize: `Yes`
   - **Otherwise**: Orientation is **rejected**. The API returns HTTP 422 with the reason. The user must provide an explicit orientation and enable resize.

### Inputs:

| Parameter | Source | Default |
|-----------|--------|---------|
| `width_mm` | From Step 2 | — |
| `height_mm` | From Step 2 | — |
| `tape_width_mm` | `config.printing.tape_width_mm` | `62` |
| `requested_orientation` | API request body (`/api/print`, `/api/preview`) | `None` (auto-detect) |
| `resize` | API request body | `False` |

### Output: `OrientationResult`

| Field | Type | Description |
|-------|------|-------------|
| `accepted` | `bool` | Whether the orientation is valid |
| `orientation` | `str\|None` | `"portrait"` or `"landscape"` |
| `rotation` | `int` | 0 or 90 degrees |
| `needs_resize` | `bool` | Whether the image must be resized to fit tape |
| `reason` | `str` | Human-readable explanation |

---

## 4. Conversion to PNG

**Module**: `app/converter.py` → `convert_to_png()`

Converts the uploaded file to one or more PNG images at the printer's exact pixel resolution. Returns a tuple of `(png_paths, debug_info)`.

### Tape Width → Pixel Width Mapping

The target pixel width is determined by the tape width:

| Tape Width (mm) | Printable Pixels (300 DPI) |
|-----------------|---------------------------|
| 12 | 106 |
| 29 | 306 |
| 38 | 413 |
| 50 | 554 |
| 54 | 590 |
| 62 | 696 |
| 102 | 1164 |
| 103 | 1200 |

### PDF Conversion (`_convert_pdf()`)

**Critical**: `pdftoppm -scale-to-x` / `-scale-to-y` does **NOT** preserve aspect ratio when only one axis is specified. To work around this, the exact DPI is computed from the PDF's page dimensions and the target pixel size, then `pdftoppm -r {dpi}` is used instead.

1. Read PDF page dimensions in mm via `get_pdf_dimensions_mm()`.
2. Convert to inches: `w_in = w_mm / 25.4`, `h_in = h_mm / 25.4`.
3. Compute DPI:
   - **Portrait** (scale axis = x): `dpi = round(target_pixels / w_in)`
   - **Landscape** (scale axis = y): `dpi = round(target_pixels / h_in)`
4. Run: `pdftoppm -png -r {dpi} {input} {output_prefix}`
5. This produces one PNG per page, each at the exact target pixel dimensions with correct aspect ratio.

**Example**: 62×27mm PDF, portrait, 696px target:
- DPI = round(696 / 2.4409) = 285
- Output: 696×303px (aspect 2.297, matches PDF aspect 2.296)

### SVG Conversion (`_convert_svg()`)

Uses `rsvg-convert -w {pixel_width} -f png -o {output} {input}`. Rasterizes directly at the target pixel width.

### Image Conversion (`_convert_image()`)

Uses Pillow to open, convert to RGB if needed, and save as PNG. No resizing is performed at this stage — the image is kept at its original pixel dimensions. Resizing (if needed) happens in **Step 5: Image Preparation**.

### Multi-page PDFs

For multi-page PDFs, `pdftoppm` produces one PNG per page. Each page is processed independently through the remaining pipeline steps.

---

## 5. Image Preparation (`prepare_image_for_print()`)

**Module**: `app/printer.py` → `prepare_image_for_print()`

Prepares the converted PNG for printing. This step is called both during preview generation and before printing.

### Conditional branches:

1. **`needs_resize = True`** (neither dimension matched tape width):
   - Calls `resize_for_print()`:
     - **Portrait**: Scale width to tape pixel width, maintain aspect ratio (LANCZOS).
     - **Landscape**: Scale height to tape pixel width, maintain aspect ratio (LANCZOS), then rotate 90°.
   - Saves the resized image.

2. **`needs_resize = False`, orientation = `landscape`**:
   - Opens the PNG, rotates 90° (with `expand=True`), saves.

3. **`needs_resize = False`, orientation = `portrait`**:
   - Opens the PNG, converts to RGB if needed, saves as-is (no transformation).

**Output**: A prepared PNG file at `{data_dir}/prints/print_{uuid}.png` (or `prepared_{uuid}.png` for previews).

---

## 6. Preview Generation (optional)

**Module**: `app/converter.py` → `generate_preview()`
**Endpoint**: `POST /api/preview`

### When it runs:

- **Automatic**: After successful upload, if `config.ui.show_preview` is `true` and orientation was auto-detected (accepted). The frontend triggers `btn-preview` click automatically.
- **Manual**: User clicks "Generate Preview" button in the UI.
- **API**: Direct call to `POST /api/preview` with `file_id`, optional `orientation`, `resize`, and `label`.

### Process:

1. Find the uploaded file by `file_id` in `{data_dir}/uploads/`.
2. Re-run **Step 2** (dimension detection) and **Step 3** (orientation detection) with any user-provided overrides.
3. If orientation is not accepted → HTTP 422.
4. Run **Step 4** (conversion to PNG) — produces one PNG per page.
5. For each page, run **Step 5** (image preparation) to produce the final print-ready image.
6. For each page, call `generate_preview()`:
   - Opens the prepared PNG.
   - Scales down to max 400px on the longest side (LANCZOS) if the image is larger.
   - Saves to `{data_dir}/previews/{preview_id}.png`.
7. Returns a list of preview objects (one per page) with `preview_url`, `page_num`, and debug info.

### Multi-page previews:

The frontend receives an array of preview objects and displays them in a carousel with prev/next navigation buttons.

---

## 7. Print Queueing (`POST /api/print`)

**Module**: `app/main.py` → `print_file()`

### Process:

1. Find the uploaded file by `file_id`.
2. Run **Step 2** (dimension detection) and **Step 3** (orientation detection) with any user-provided overrides.
3. If orientation is not accepted → HTTP 422.
4. Run **Step 4** (conversion to PNG) — produces one PNG per page.
5. **All-or-nothing processing**: For each page, run **Step 5** (image preparation).
   - If **any page** fails preparation → all already-prepared pages are deleted, HTTP 422 returned. Nothing is queued.
6. Generate a preview thumbnail for the first page (if `show_preview` is enabled).
7. Create a single `QueueItem` with:
   - `stored_filenames`: list of all prepared PNG paths
   - `num_pages`: number of pages
   - `debug_info`: full debug string with commands, dimensions, and intermediary info
   - `status`: `"queued"`
8. Add to queue and trigger background processing.

### QueueItem fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | UUID |
| `original_filename` | `str` | Original uploaded filename |
| `stored_filename` | `str` | First prepared PNG path (backward compat) |
| `stored_filenames` | `list[str]` | All prepared PNG paths |
| `num_pages` | `int` | Number of pages |
| `timestamp` | `str` | ISO 8601 UTC timestamp |
| `status` | `str` | `queued`, `printing`, `printed`, `failed` |
| `label` | `str` | Label type (e.g. `"62"`) |
| `rotation` | `int` | Rotation in degrees (0 or 90) |
| `copies` | `int` | Number of copies |
| `orientation` | `str` | `"portrait"` or `"landscape"` |
| `width_mm` | `float` | Original document width |
| `height_mm` | `float` | Original document height |
| `error_message` | `str` | Error details if failed |
| `page_error` | `str` | Per-page error (for partial failures) |
| `preview_filename` | `str` | Preview thumbnail filename |
| `debug_info` | `str` | Debug details (commands, dimensions, intermediaries) |

---

## 8. Queue Processing (background)

**Module**: `app/main.py` → `process_queue()`

Runs as a background asyncio task. Only one instance processes at a time (guarded by `_queue_processing` flag).

### Process:

1. Get the first item with `status == "queued"`.
2. Set status to `"printing"`.
3. Determine files to print: `item.stored_filenames` (or fallback to `[item.stored_filename]`).
4. Build the print sequence based on `copy_order` setting:

   - **`sequential`** (default): All pages in order, repeated per copy.
     - Example: 2 pages, 3 copies → `[p1, p2, p1, p2, p1, p2]`
   - **`grouped`**: Each page printed all copies before moving to next.
     - Example: 2 pages, 3 copies → `[p1, p1, p1, p2, p2, p2]`

5. For each file in the print sequence, call `do_print()` (in a thread via `asyncio.to_thread`).

### Error handling (`on_print_error` setting):

- **`stop`** (default): If any page fails to print, the entire job is marked as `failed` with the error message. No further pages in the sequence are printed.
- **`continue`**: If a page fails, the error is recorded in `page_error`, and printing continues with the next page in the sequence. After all pages are attempted:
  - If any page failed: status = `failed`, `error_message` = `"Partial failure: ..."`, `page_error` = error details.
  - If all pages succeeded: status = `printed`.

### Print call parameters:

Each `do_print()` call uses these settings from `config.printer` and `config.printing`:

| Parameter | Config Source | Default |
|-----------|--------------|---------|
| `model` | `printer.model` | `QL-560` |
| `backend` | `printer.backend` | `pyusb` |
| `printer_identifier` | `printer.identifier` | `usb://04f9:2027` |
| `label` | `item.label` or `printer.label` | `62` |
| `rotate` | `item.rotation` or `"auto"` | `auto` |
| `threshold` | `printing.threshold` | `70` |
| `dither` | `printing.dither` | `false` |
| `compress` | `printing.compress` | `false` |
| `cut` | `printing.cut` | `true` |
| `hq` | `printing.hq` | `true` |
| `dpi_600` | `printing.dpi_600` | `false` |

---

## 9. Printing (`do_print()`)

**Module**: `app/printer.py` → `do_print()`

Sends the prepared PNG to the printer using `brother_ql_next`.

1. Create a `BrotherQLRaster` instance for the printer model.
2. Set `exception_on_warning = True` (warnings are treated as errors).
3. Call `convert()` to rasterize the PNG into printer instructions:
   - Applies threshold/dithering for B/W conversion.
   - Applies rotation if specified.
   - Handles compression, cut, HQ mode, 600 DPI mode.
4. Call `send()` to transmit instructions to the printer via the configured backend.
   - `blocking=True` — waits for the print to complete.

**On error**: Exception propagates to the queue processor (Step 8), which handles it based on `on_print_error` setting.

---

## 10. Debug Info

Every print job stores a `debug_info` string in the queue item, visible as a collapsible section in the History page.

### Contents:

- The exact `pdftoppm` command used (or `rsvg-convert` / Pillow info)
- PDF page dimensions in mm and inches
- Computed DPI and target pixel dimensions
- Raw PNG dimensions for each page
- Prepared image dimensions for each page
- Original document dimensions
- Orientation, rotation, needs_resize flag
- Tape width and corresponding pixel width
- Number of copies and pages
- Label type
- Final print image paths and dimensions

### Example:

```
pdftoppm command: pdftoppm -png -r 285 /data/uploads/abc.pdf /data/prints/abc
PDF page: 62.0x27.0mm (2.4409x1.0630in)
Computed DPI: 285 (target x=696px)
pdftoppm produced 1 page(s)
  Page 1 raw PNG: abc-1.png — 696x303px (mode=RGB)
  Page 1 prepared: /data/prints/print_xyz.png — 696x303px
Original dimensions: 62.0x27.0mm
Orientation: portrait, rotation: 0°
needs_resize: False
Tape width: 62mm (pixel_width: 696)
Copies: 1, Pages: 1
Label: 62
Final print image 1: /data/prints/print_xyz.png — 696x303px
```

---

## Configuration Reference

All pipeline-relevant settings in `config.yaml`:

| Section | Key | Default | Affects |
|---------|-----|---------|--------|
| `printer.model` | `QL-560` | Step 9: printer model |
| `printer.backend` | `pyusb` | Step 9: connection method |
| `printer.identifier` | `usb://04f9:2027` | Step 9: printer address |
| `printer.label` | `62` | Step 8-9: label type |
| `printing.tape_width_mm` | `62` | Steps 3-5: target pixel width |
| `printing.rotate` | `auto` | Step 9: rotation override |
| `printing.threshold` | `70` | Step 9: B/W threshold |
| `printing.dither` | `false` | Step 9: dithering |
| `printing.compress` | `false` | Step 9: compression |
| `printing.cut` | `true` | Step 9: auto-cut |
| `printing.hq` | `true` | Step 9: high quality |
| `printing.dpi_600` | `false` | Step 9: 600 DPI mode |
| `printing.copy_order` | `sequential` | Step 8: copy ordering |
| `printing.on_print_error` | `stop` | Step 8: error handling |
| `ui.show_preview` | `true` | Step 6: auto-preview after upload |

---

## File Flow Summary

```
Upload:
  uploads/{file_id}.{ext}
         │
         ▼
  get_original_dimensions_mm()  →  (width_mm, height_mm)
         │
         ▼
  determine_orientation()       →  OrientationResult
         │
         ▼
  convert_to_png()              →  prints/{base}-1.png, {base}-2.png, ...
         │
         ▼
  prepare_image_for_print()     →  prints/print_{uuid}.png  (or prepared_{uuid}.png for previews)
         │
         ├─ (preview) → generate_preview() → previews/{preview_id}.png
         │
         └─ (print)   → QueueItem(stored_filenames=[...])
                              │
                              ▼
                        process_queue() → do_print() → Printer
```
