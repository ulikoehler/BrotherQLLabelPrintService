#!/usr/bin/env bash
set -euo pipefail

DOCKER_USER="ulikoehler"
IMAGE_NAME="brotherql-label-print-service"
FULL_IMAGE="${DOCKER_USER}/${IMAGE_NAME}"

# Optional version tag from first argument, defaults to "latest"
TAG="${1:-latest}"

echo "==> Building ${FULL_IMAGE}:${TAG}"
docker build -t "${FULL_IMAGE}:${TAG}" .

# Always also tag as latest
if [ "$TAG" != "latest" ]; then
    docker tag "${FULL_IMAGE}:${TAG}" "${FULL_IMAGE}:latest"
fi

echo "==> Pushing ${FULL_IMAGE}:${TAG}"
docker push "${FULL_IMAGE}:${TAG}"

if [ "$TAG" != "latest" ]; then
    echo "==> Pushing ${FULL_IMAGE}:latest"
    docker push "${FULL_IMAGE}:latest"
fi

echo "==> Done. Image available at https://hub.docker.com/r/${DOCKER_USER}/${IMAGE_NAME}"
