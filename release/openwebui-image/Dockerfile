# syntax=docker/dockerfile:1.7
# Memorist Open WebUI derivative image (PR5-G).
#
# Reproducibility contract (see source-pin.json):
#   * frontend is rebuilt from the pinned Open WebUI 0.9.6 PyPI source
#     snapshot (sha256-verified download; not a git clone) with the Memorist
#     overlay + reviewed patch layer applied;
#   * the runtime layer starts from the pinned upstream container image by
#     immutable digest and only replaces the compiled frontend and adds the
#     Memorist integration package;
#   * the backend entrypoint is the Memorist production entrypoint, which
#     fail-closes on version drift, route-order violations, and filter
#     provisioning failures.
#
# Build context: the repository root (or the extracted package's runtime/
# directory, which mirrors the same layout). Both provide:
#   openwebui-image/  (this directory: patches, overlay, pin, scripts)
#   open-webui-integration/memorist/  (backend + shared + filter + ui)
ARG OPENWEBUI_BASE=ghcr.io/open-webui/open-webui:v0.9.6@sha256:90eae5b419e40b4c3dd684582b2c83440b36f9ae2f6532c09639b2ba4ee65158
ARG NODE_BUILDER=docker.io/library/node:22-alpine3.20@sha256:2289fb1fba0f4633b08ec47b94a89c7e20b829fc5679f9b7b298eaa2f1ed8b7e
ARG OPENWEBUI_SDIST_URL=https://files.pythonhosted.org/packages/3a/c7/3d5d4465e7bb7fc1832f0e63eaf988d847af5c8dbc9595d342fc681dbfdf/open_webui-0.9.6.tar.gz
ARG OPENWEBUI_SDIST_SHA256=91b38fa35917bb7d2f645d030ed3b9544d35b72354bce3098d059899fd7f688b

FROM ${NODE_BUILDER} AS frontend
ARG OPENWEBUI_SDIST_URL
ARG OPENWEBUI_SDIST_SHA256
WORKDIR /build
RUN apk add --no-cache patch tar ca-certificates curl \
    && curl -fsSL --retry 4 --connect-timeout 30 \
        -o /build/openwebui-src.tar.gz "${OPENWEBUI_SDIST_URL}" \
    && echo "${OPENWEBUI_SDIST_SHA256}  /build/openwebui-src.tar.gz" | sha256sum -c -
COPY openwebui-image/ /build/openwebui-image/
COPY open-webui-integration/memorist/ui/ /build/memorist-ui/
RUN sh /build/openwebui-image/prepare_frontend_tree.sh \
        /build/openwebui-src.tar.gz \
        /build/src-tree \
        /build/openwebui-image \
        /build/memorist-ui
WORKDIR /build/src-tree
ENV APP_BUILD_HASH=memorist-pr5g
RUN npm ci --force \
    && npm run build \
    && test -f build/index.html \
    && grep -rl "Memory Setup" build/_app >/dev/null \
    && grep -rl "Processing Nodes" build/_app >/dev/null \
    && grep -rl "Memory used" build/_app >/dev/null \
    && grep -rl "memorist-memory-workflow-toggle" build/_app >/dev/null

FROM ${OPENWEBUI_BASE}
# Replace the compiled frontend with the Memorist-integrated build.
RUN rm -rf /app/build
COPY --from=frontend /build/src-tree/build /app/build
# Ship the Memorist integration package (backend router, entrypoint, shared
# client, managed filter) inside the image so it survives container
# recreation without bind mounts.
COPY open-webui-integration/memorist /memorist-integration/memorist
ENV PYTHONPATH=/memorist-integration
LABEL org.opencontainers.image.title="Memorist Open WebUI" \
      org.opencontainers.image.description="Open WebUI v0.9.6 with the integrated Memorist memory product" \
      org.opencontainers.image.base.name="ghcr.io/open-webui/open-webui:v0.9.6"
CMD ["python", "-m", "memorist.backend.openwebui_entrypoint"]
