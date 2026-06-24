FROM python:3.11-slim

# System dependencies:
#   poppler-utils  — provides pdftoppm for PDF → PNG conversion
#   librsvg2-bin   — provides rsvg-convert for SVG → PNG conversion
#   libusb-1.0-0   — required by pyusb backend for USB printer access
#   libglib2.0-0   — runtime dependency for rsvg-convert
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    librsvg2-bin \
    libusb-1.0-0 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Data directory is mounted as a volume
VOLUME ["/data"]

# Config file is mounted as a volume
VOLUME ["/app/config.yaml"]

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
