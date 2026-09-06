# syntax=docker/dockerfile:1

# Two stages: uv resolves and installs into a virtualenv, the runtime image copies only
# that venv plus the source. The runtime carries no uv, no compiler and no dev extras.

# Pinned to an exact uv release. The published images lag the CLI, so this is not
# whatever `uv --version` says locally — it is the newest tag that exists, and it
# reads the committed uv.lock (`uv lock --check` passes against it).
FROM ghcr.io/astral-sh/uv:0.9.30-python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, without the project itself: this layer is keyed on uv.lock alone, so
# it is reused across every build that does not change a dependency.
# --locked fails the build if uv.lock is out of date with pyproject.toml, matching CI.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --locked --no-dev --no-install-project

COPY pyproject.toml uv.lock ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev


FROM python:3.12-slim-bookworm AS runtime

# The venv from the builder shares this image's interpreter, so it is copied, not rebuilt.
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    PORT=8501

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app src ./src
# The theme, mirrored onto Streamlit's own tokens. Secrets are never baked in: the
# entrypoint writes .streamlit/secrets.toml from the environment at start-up.
COPY --chown=app:app .streamlit/config.toml ./.streamlit/config.toml
COPY --chown=app:app docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER app

EXPOSE 8501

# curl is not in the slim base; Streamlit's own health endpoint answers over stdlib http.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT','8501') + '/_stcore/health', timeout=4)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
