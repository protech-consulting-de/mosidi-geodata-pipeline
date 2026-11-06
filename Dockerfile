FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY . /app
RUN python -m pip install --no-cache-dir -r requirements.lock \
    && python -m pip install --no-cache-dir --no-deps . \
    && mkdir -p /app/reports /app/.cache

# Compose drops all Linux capabilities, makes the image read-only and exposes
# only the report/cache directories for writes. No database init runs at startup.
ENTRYPOINT ["python", "-m", "geopipeline"]
CMD ["--config", "config/demo.yaml", "check"]
