FROM public.ecr.aws/docker/library/golang:1.24.2-bookworm AS build
ARG MINIO_COMMIT=0d7408fc9969caf07de6a8c3a84f9fbb10a6739e
ENV GOTOOLCHAIN=local GOMAXPROCS=2 CGO_ENABLED=0
WORKDIR /src
RUN git init \
    && git remote add origin https://github.com/minio/minio.git \
    && for attempt in 1 2 3; do \
         if git -c http.version=HTTP/1.1 fetch --depth 1 origin ${MINIO_COMMIT}; then break; fi; \
         test "$attempt" -lt 3 || exit 1; \
       done \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${MINIO_COMMIT}"
RUN go build -p 2 -trimpath -tags kqueue \
    -ldflags "$(go run buildscripts/gen-ldflags.go)" -o /out/minio .

FROM public.ecr.aws/docker/library/debian:bookworm-slim
RUN apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/minio /usr/bin/minio
COPY --from=build /src/LICENSE /usr/share/doc/minio/LICENSE
LABEL org.opencontainers.image.source="https://github.com/minio/minio" \
      org.opencontainers.image.revision="0d7408fc9969caf07de6a8c3a84f9fbb10a6739e" \
      org.opencontainers.image.version="RELEASE.2025-04-22T22-12-26Z" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
EXPOSE 9000 9001
CMD ["minio", "server", "/data", "--console-address", ":9001"]
