# Build stage: install the package under /out for the runtime stage to copy, so
# pip and the source never reach the image.
FROM alpine:3.24 AS build

RUN apk add --no-cache python3 py3-pip
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# --break-system-packages: the install lands under /out, not the system tree.
# --no-compile: the /out paths would be baked into the bytecode. --only-binary:
# a cryptography release missing its musllinux wheel must fail here, not fall
# back to building Rust.
RUN pip install --no-cache-dir --break-system-packages --no-compile \
    --only-binary=cryptography --root=/out --prefix=/usr .

# Web build stage: Node exists only here. The UI compiles to static files.
FROM node:22-alpine AS web

WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
RUN npm run build

# Alpine's ffmpeg carries the native AAC encoder, which is all the downmixes
# need. No hardware acceleration: the work is stream copies and is disk-bound.
FROM alpine:3.24

RUN apk add --no-cache ffmpeg python3 tzdata
COPY --from=build /out /
COPY --from=web /web/build /web
RUN python3 -m compileall -q /usr/lib/python3*/site-packages/trackstarr

ENV PYTHONUNBUFFERED=1
# Where the web stage landed the UI; WEB_DIR= turns the pages off.
ENV WEB_DIR=/web
EXPOSE 5120

# So a bare `docker run` does not run as root and write root-owned files the
# next non-root start cannot read. A numeric id needs no passwd entry, and
# `--user` still wins.
USER 1000:1000

# Only serve opens a port, so only serve is probed; a one-shot `sweep` would
# otherwise sit at unhealthy for a restarter to act on. LISTEN_PORT rather
# than a literal, or moving the listener leaves the container unhealthy.
HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
  CMD tr '\0' '\n' < /proc/1/cmdline | grep -qx serve || exit 0; \
      wget -q --spider -T 5 "http://127.0.0.1:${LISTEN_PORT:-5120}/health" || exit 1

ENTRYPOINT ["trackstarr"]
CMD ["serve"]
