# Build stage: install the package under /out for the runtime stage to copy.
# pip and the source tree never reach the final image, and nothing has to be
# deleted after the fact — a file removed in a later layer still ships in the
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
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
  CMD wget -q --spider -T 5 http://127.0.0.1:8080/health || exit 1

ENTRYPOINT ["trackstarr"]
CMD ["serve"]
