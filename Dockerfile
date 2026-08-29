FROM python:3.11-slim

# System dependencies for opencv, tesseract, pillow, psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    tesseract-ocr \
    libtesseract-dev \
    libgl1 \
    libglib2.0-0 \
    libjpeg62-turbo \
    libpng16-16 \
    libtiff6 \
    libwebp7 \
    libopenjp2-7 \
    zlib1g \
    libfreetype6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install UV for deterministic dependency resolution
RUN pip install --no-cache-dir uv

# Copy lock files first (cache layer)
COPY pyproject.toml uv.lock ./

# Install ALL dependencies from uv.lock (deterministic, including prophet wheel)
RUN uv sync --frozen --no-dev

# Copy application code
COPY . .

# Create uploads directory
RUN mkdir -p /app/static/uploads

# Expose port
EXPOSE 5000

# Volume for persistent uploads
VOLUME ["/app/static/uploads"]

# Run with Gunicorn using config file (uses uv's venv)
CMD [".venv/bin/gunicorn", "-c", "gunicorn.conf.py", "main:app"]
