# Multi-stage Dockerfile: 3 stages
# 1. frontend-builder: builds React/Vite static assets
# 2. python-builder: installs Python deps in a layer-cached stage
# 3. runtime: slim final image with non-root user and HEALTHCHECK

# ── Stage 1: Build React frontend ────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend

# Install deps first (cached layer — only re-runs if package*.json changes)
COPY frontend/package*.json ./
RUN npm ci --silent

# Build
COPY frontend/ ./
RUN npm run build


# ── Stage 2: Install Python dependencies ─────────────────────────────────────
FROM python:3.12-slim AS python-builder
WORKDIR /build

# gcc needed for some scientific library builds (scipy, statsmodels)
RUN apt-get update && apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --prefix=/install .


# ── Stage 3: Slim runtime ─────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime
WORKDIR /app

# Non-root user — production security practice.
# uid 1000 is a standard non-privileged UID.
RUN useradd --system --uid 1000 --no-create-home appuser

# Copy installed Python packages from builder layer
COPY --from=python-builder /install /usr/local

# Copy frontend static build
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Copy application source and config
COPY src/ ./src/
COPY prompts/ ./prompts/
COPY config.yaml ./

# Bake the stable corpus and pre-computed sweep results into the image.
# These are committed to the repo (required deliverables) and are static —
# the container can serve the recommendation and sweep view without any
# runtime data fetch.
# Note: individual cache files (data/cache/) are NOT baked in — they are
# gitignored and should be mounted as a volume for persistence across restarts.
COPY data/issues.json ./data/
COPY data/issues.sha256 ./data/
COPY data/ground_truth.json ./data/
COPY data/sweep_results.json ./data/

# Writable dirs for runtime artifacts — owned by appuser
RUN mkdir -p data/cache data/runs data/manifests && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

# Runtime configuration — all overrideable at container start, no rebuild needed.
# CONCURRENCY is Principle 3: "configurable without rebuilding the container"
ENV CONCURRENCY=10
ENV PORT=8080

# Healthcheck — used by docker-compose depends_on and App Platform health routing
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')" || exit 1

CMD ["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
