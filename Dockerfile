# VoxNova: one image, one process. server.py serves the page, the static files
# and the WebSocket the voice streams over.
#
#   scripts/build-image.sh            # build for linux/amd64 and load it locally
#   PUSH=1 scripts/build-image.sh     # build and push to the starter-kits registry

ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Listen on every interface: 127.0.0.1 inside a container is unreachable from outside it.
    HOST=0.0.0.0 \
    PORT=8080 \
    # Customer memory lives on the persistent volume, not in the image.
    MEMORY_FILE=/data/memory.sqlite3

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt \
    && useradd --create-home --uid 1000 --shell /usr/sbin/nologin voxnova \
    && mkdir -p /data && chown voxnova:voxnova /data

# Secrets never go in the image: .dockerignore keeps .env, the local memory
# database and everything else that is not the app out of the build.
COPY --chown=voxnova:voxnova . .

USER 1000
EXPOSE 8080
VOLUME ["/data"]
CMD ["python", "server.py"]
