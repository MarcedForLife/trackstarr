# Build stage: install the package under /out for the runtime stage to copy.
# pip and the source tree never reach the final image, and nothing has to be
# deleted after the fact, a file removed in a later layer still ships in the
# earlier one.
FROM alpine:3.24 AS build

RUN apk add --no-cache python3 py3-pip
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# --break-system-packages: Alpine marks its Python as externally managed, but
# the install lands under /out, not in the system tree, and this stage is
# thrown away regardless. --no-compile because the /out paths would be baked
# into the bytecode; the runtime stage compiles against the real ones.
RUN pip install --no-cache-dir --break-system-packages --no-compile \
    --root=/out --prefix=/usr .

# Alpine's ffmpeg carries the native AAC encoder, which is all the downmixes
# need. No GPU and no hardware acceleration: every rule is a stream copy plus
# at most a few audio downmix encodes, so the work is disk-bound.
FROM alpine:3.24

RUN apk add --no-cache ffmpeg python3 tzdata
COPY --from=build /out /
RUN python3 -m compileall -q /usr/lib/python3*/site-packages/trackstarr

ENV PYTHONUNBUFFERED=1
EXPOSE 5120

# The default the compose example spells out anyway, so that a bare `docker
# run` is not the one way to end up as root, writing root-owned files into
# /config and the library, which the next non-root start then cannot read.
# A numeric id needs no passwd entry, and `user:` or `--user` still wins, so
# a stack on different ids is unaffected.
USER 1000:1000

# Only serve opens a port, so probe only serve: a one-shot `docker run
# trackstarr sweep` has no listener and would otherwise sit at unhealthy for
# its whole run, which is what a restarter watches for. Docker has no "not
# applicable" health status, so those commands report healthy instead.
# LISTEN_PORT rather than a literal 5120, or moving the listener would leave
# the container permanently unhealthy.
HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
  CMD tr '\0' '\n' < /proc/1/cmdline | grep -qx serve || exit 0; \
      wget -q --spider -T 5 "http://127.0.0.1:${LISTEN_PORT:-5120}/health" || exit 1

ENTRYPOINT ["trackstarr"]
CMD ["serve"]
