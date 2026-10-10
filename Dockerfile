# GlucoTwin backend container.
#
# STATUS: WRITTEN BUT NOT YET BUILT OR RUN. The development machine has no Docker
# daemon, so this file is unverified. Do not claim containerized deployment in the
# presentation until `docker build` and `docker run` have actually been executed and
# the smoke checks at the bottom of docs/deployment.md pass.
#
# The verified path today is the local one in README.md.

FROM python:3.12-slim AS base

# Fail fast and log straight through: no .pyc clutter, no buffered stdout in a
# container runtime where buffered logs are lost on crash.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so a source edit does not invalidate the wheel layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY contracts/ ./contracts/
COPY ml/ ./ml/
COPY backend/ ./backend/

# Train inside the image so the container ships with a working artifact. Without
# this the service starts healthy but every /api/predict returns 503
# model_not_trained, which is correct behaviour and a useless deployment.
# Deterministic, so the artifact is reproducible from this Dockerfile alone.
RUN python -m ml.train --source synthetic_dev \
 && python -m ml.evaluate --protocol all

# Run unprivileged.
RUN useradd --create-home --uid 10001 glucotwin \
 && chown -R glucotwin:glucotwin /app
USER glucotwin

ENV GLUCOTWIN_CONTRACT_VERSION=1.0.0 \
    GLUCOTWIN_MODEL_ARTIFACT=ml/artifacts/model.joblib \
    GLUCOTWIN_METRICS_PATH=ml/artifacts/metrics.json \
    GLUCOTWIN_DATA_BACKEND=synthetic_dev \
    GLUCOTWIN_LOG_LEVEL=INFO \
    PORT=8000

EXPOSE 8000

# Probes liveness only, matching the contract: /health is deliberately independent of
# model readiness so an orchestrator can tell "process up" from "model ready".
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,os,sys; \
sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8000)}/health', timeout=4).status==200 else 1)"

# Shell form so ${PORT} is expanded by the platform at runtime.
CMD uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT}
