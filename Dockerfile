# =============================================================================
# CivicOS — Dockerfile
# =============================================================================
# Multi-stage build:
#   builder  → install Python dependencies
#   final    → lean runtime image
#
# Build:
#   docker build --target development -t civicos:dev .
#   docker build --target production  -t civicos:prod .
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Python dependency builder
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

WORKDIR /build

# System dependencies needed to compile Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Install pip-tools for reproducible installs
RUN pip install --no-cache-dir pip-tools==7.*

COPY requirements/ ./requirements/

# Install base requirements into a local directory for copying
RUN pip install --no-cache-dir --prefix=/install -r requirements/base.txt

# ---------------------------------------------------------------------------
# Stage 2: Development image
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS development

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    DJANGO_SETTINGS_MODULE=config.settings.development \
    PORT=8000

WORKDIR /app

# Runtime system dependencies (includes WeasyPrint GTK/Pango/Cairo stack)
# libmagic1 — required by python-magic (Documents BB Layer 5 magic-byte MIME
# sniffing in apps/documents/services/upload.py); python-magic is a ctypes
# wrapper and does nothing useful without the actual libmagic shared library.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    libglib2.0-0 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    fonts-liberation \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Install dev dependencies on top of base
# Copy full requirements/ dir because development.txt references base.txt via -r
COPY requirements/ ./requirements/
RUN pip install --no-cache-dir -r requirements/development.txt

COPY . .

# Collect static files
RUN python manage.py collectstatic --noinput 2>/dev/null || true

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

# ---------------------------------------------------------------------------
# Stage 3: Production image
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS production

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    PORT=8000

WORKDIR /app

# Create non-root user for security
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --shell /bin/bash --create-home appuser

# Runtime system dependencies only (includes WeasyPrint GTK/Pango/Cairo stack)
# libmagic1 — required by python-magic (Documents BB Layer 5 magic-byte MIME
# sniffing in apps/documents/services/upload.py); python-magic is a ctypes
# wrapper and does nothing useful without the actual libmagic shared library.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    libglib2.0-0 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    fonts-liberation \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Install production extras
COPY requirements/ ./requirements/
RUN pip install --no-cache-dir -r requirements/production.txt

# Copy application code
COPY --chown=appuser:appgroup . .

# Collect static files with a build-only bootstrap environment.
# Keep Django in debug mode here so startup guards for runtime-only services
# (for example production Fernet/S3 requirements) do not break the image build.
RUN DJANGO_SECRET_KEY=build-time-key \
    DJANGO_DEBUG=True \
    FERNET_KEYS=bxYAibKUFsiGuQex4mPBNvcc-4kLxafPgtVxpLp8rQA= \
    VOLUNTEER_SIN_FERNET_KEYS=FlpigUc6aS6MU9K_XLd5kZiqCTwfnaFBEBuY5Xh_agY= \
    DJANGO_SETTINGS_MODULE=config.settings.base \
    DATABASE_URL=postgres://x:x@localhost/x \
    python manage.py collectstatic --noinput

# Drop to non-root
USER appuser

EXPOSE 8000

# Health check — hits the /health/ endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health/ || exit 1

CMD ["gunicorn", "--config", "gunicorn.conf.py", "config.wsgi:application"]
