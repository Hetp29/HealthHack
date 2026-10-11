# GlucoTwin backend container: frozen v1 API + /api/v2, and the three workers.
#
# STATUS: WRITTEN BUT NOT YET BUILT OR RUN. The development machine has no Docker
# daemon, so this file is unverified. Do not describe containerized deployment as
# working until `docker build` and `docker run` have actually been executed and
# `python scripts/smoke_v2.py <url>` passes against the container.
#
# THE MODEL IS NEVER TRAINED HERE. The image carries exactly the frozen fs-1.3
# artifact, supplied in the build context, and the build fails if it is absent or is
# not byte-for-byte the approved file. There is no fallback to a synthetic model.
#
# Build context requirement: ml/artifacts_cgmacros_fs13/ must be present (it is
# gitignored; obtain it from the team's artifact store -- docs/project/HANDOFF_HET.md).

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so a source edit does not invalidate the wheel layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY contracts/ ./contracts/
COPY data/ ./data/
COPY ml/ ./ml/
COPY backend/ ./backend/
COPY workers/ ./workers/
COPY migrations/ ./migrations/
COPY alembic.ini ./

ENV GLUCOTWIN_CONTRACT_VERSION=1.0.0 \
    GLUCOTWIN_MODEL_ARTIFACT=ml/artifacts_cgmacros_fs13/model.joblib \
    GLUCOTWIN_METRICS_PATH=ml/artifacts_cgmacros_fs13/metrics.json \
    GLUCOTWIN_EXPECTED_MODEL_SHA256=12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe \
    GLUCOTWIN_DATA_BACKEND=synthetic_dev \
    GLUCOTWIN_LOG_LEVEL=INFO \
    PORT=8000

# Build-time gate: no artifact, or the wrong artifact, and there is no image.
# Then drop every other model directory so nothing else can be pointed at by mistake.
RUN python -m backend.app.v2.verify_artifact \
 && find ml -maxdepth 1 -type d -name 'artifacts*' ! -name 'artifacts_cgmacros_fs13' -exec rm -rf {} + \
 && rm -rf ml/_devdata data/local

# Run unprivileged, with the artifact read-only even to the service user.
RUN useradd --create-home --uid 10001 glucotwin \
 && mkdir -p /app/var/meal_images \
 && chown -R glucotwin:glucotwin /app/var \
 && chmod -R a-w ml/artifacts_cgmacros_fs13
USER glucotwin
ENV GLUCOTWIN_IMAGE_DIR=/app/var/meal_images

EXPOSE 8000

# Liveness only. Readiness (database + model + checksum) is GET /api/v2/ready.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,os,sys; \
sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8000)}/health', timeout=4).status==200 else 1)"

# Start-time gate, then serve. If the artifact is not the approved one the container
# exits instead of serving -- this is what protects the frozen v1 endpoints, which
# cannot check for themselves. Migrations are NOT run here: apply `alembic upgrade
# head` as a separate one-off task before rolling out.
CMD python -m backend.app.v2.verify_artifact \
 && exec uvicorn backend.app.v2.asgi:app --host 0.0.0.0 --port ${PORT}
