# syntax=docker/dockerfile:1.7
#
# One image serves the JSON API and the built React UI at the same origin.
#
# Stage 1 builds ui/dist with Node — it is gitignored, so it cannot come from
# the repo. Stage 2 is the Python runtime. The package is installed *editable*
# on purpose: `src/core/config.py` derives REPO_ROOT from its own location, so
# a regular `pip install .` would relocate the default DB, logs, reports and
# ui/dist into site-packages. Editable keeps everything under /app.
#
# Runtime configuration is environment-only (see .env.example, "Railway" block):
#   HOST=0.0.0.0  PORT=<injected>  ENVIRONMENT=production
#   CONTROL_PLANE_USER / CONTROL_PLANE_PASSWORD   (required in production)
#   TRACKER_DB_PATH=/data/prompt_tracker.sqlite   (mount a volume at /data)
#   plus the vendor keys and spend caps.

# ---- Stage 1: front end ---------------------------------------------------------
FROM node:20-alpine AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
# Dev dependencies are required: `npm run build` runs tsc and vite.
RUN npm ci --no-audit --no-fund
COPY ui/ ./
RUN npm run build

# ---- Stage 2: runtime -------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first so source edits do not invalidate the layer.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install -e ".[ui]"

# Things the app reads at runtime besides the package itself.
COPY docs/ ./docs/
COPY scripts/ ./scripts/
COPY --from=ui /ui/dist ./ui/dist

# Non-root. /data is the volume mount point for the SQLite store, reports and logs.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data \
    && chown -R app:app /app /data
USER app

EXPOSE 8787

# --approve-spend is what lets the UI's Run button work at all; on a public
# host it is safe only because every route is behind Basic auth and spend is
# bounded by MAX_SESSION_SPEND_USD and DAILY_SPEND_CAP_USD. No --poll-minutes:
# a poller fires its first cycle at boot, i.e. on every redeploy.
CMD ["python", "-m", "src.modules.control_plane", "--approve-spend"]
