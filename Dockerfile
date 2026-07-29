# Invoice Processor — Multi-stage Docker build

# ── Base ──
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install system dependencies with retry logic
RUN set -e; \
    for i in 1 2 3; do \
      echo "Attempt $i: Updating package lists..."; \
      if apt-get update; then \
        echo "Attempt $i: Installing system dependencies..."; \
        if apt-get install -y --no-install-recommends \
            tesseract-ocr \
            tesseract-ocr-eng \
            tesseract-ocr-fra \
            tesseract-ocr-deu \
            tesseract-ocr-spa \
            libgl1 \
            libglib2.0-0; then \
          rm -rf /var/lib/apt/lists/*; \
          break; \
        fi; \
      fi; \
      sleep 5; \
      if [ $i -eq 3 ]; then \
        echo "Failed to install system dependencies after 3 attempts"; \
        exit 1; \
      fi; \
    done

# ── Dependencies ──
FROM base AS deps

COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

# ── Final ──
FROM deps AS final

COPY . .

RUN mkdir -p storage

EXPOSE 8000
EXPOSE 8501

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
