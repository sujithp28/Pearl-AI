# Pearl — container image for the HTTP API and web UI.
#
# Two things this image deliberately does NOT do:
#
# 1. Bake in a model. The default GGUF is ~1.1 GB, which would triple
#    the image for a file that changes independently of the code and is
#    often not wanted at all (a hosted deployment uses a remote model).
#    Mount it, or let Pearl download it on first run.
#
# 2. Run as root. Pearl executes shell commands as the server process,
#    so root here means any tool call — or anything that reaches one —
#    is root inside the container.
#
# And one thing it does not give you: isolation *between users*. One
# container running Pearl is one shared execution environment. Serving
# untrusted users needs a container per user (or per run), which is an
# orchestration decision this file cannot make. See docs/DEPLOYMENT.md.

# ---- builder -------------------------------------------------------------
# Separate stage so compilers and headers do not ship in the final image.
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# build-essential is needed only if llama-cpp-python has to compile;
# it stays in this stage either way.
RUN apt-get update && apt-get install --no-install-recommends -y \
        build-essential \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Installed into a venv so the whole tree can be copied to the runtime
# stage in one layer, without pip or its cache.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --upgrade pip && pip install .

# ---- runtime -------------------------------------------------------------
FROM python:3.11-slim AS runtime

# git: Pearl's checkpoints and verification shell out to it, so an image
# without it silently loses undo and post-apply verification.
RUN apt-get update && apt-get install --no-install-recommends -y \
        git \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged user. Created before the copies so ownership is set once.
RUN useradd --create-home --uid 10001 pearl

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY --chown=pearl:pearl src/ ./src/
COPY --chown=pearl:pearl pearl_ui/ ./pearl_ui/
COPY --chown=pearl:pearl pyproject.toml README.md ./

# The workspace Pearl operates on, and where models live. Both are
# mount points: baking either in would make the image environment
# specific and enormous respectively.
RUN mkdir -p /workspace /home/pearl/.pearl/models \
    && chown -R pearl:pearl /workspace /home/pearl/.pearl

USER pearl

ENV HOME=/home/pearl \
    PEARL_WORKSPACE=/workspace \
    PORT=7474

EXPOSE 7474

# Uses /api/status rather than / because the UI is served as a static
# file and would report healthy even with the session unusable.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/api/status" || exit 1

# python -m src.api, not a bare uvicorn: it pins the interpreter, sets
# the workspace, and is the entry point the project actually supports.
CMD ["sh", "-c", "python -m src.api --workspace \"$PEARL_WORKSPACE\" --port \"$PORT\" --no-browser"]
