# syntax=docker/dockerfile:1.10

# ---------- зависимости ----------
# Сборка идёт на платформе сборщика: wheel-файлы под целевую архитектуру скачиваются
# готовыми (--platform), без эмуляции QEMU и без компиляции.
FROM --platform=$BUILDPLATFORM python:3.13-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS deps
ARG TARGETARCH
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt .
RUN case "$TARGETARCH" in \
      amd64) ARCH=x86_64 ;; \
      arm64) ARCH=aarch64 ;; \
      *) echo "unsupported arch: $TARGETARCH" >&2; exit 1 ;; \
    esac \
 && pip install \
      --require-hashes --no-deps --only-binary=:all: \
      --platform "manylinux_2_17_${ARCH}" --platform "manylinux2014_${ARCH}" \
      --implementation cp --python-version 3.13 --abi cp313 \
      --target /install -r requirements.txt \
 && rm -rf /install/bin

COPY app /install/app
# Байт-код компилируем заранее: в контейнере файловая система только для чтения.
RUN python -m compileall -q -j 0 /install && mkdir -p /data

# ---------- финальный образ ----------
# distroless: нет shell, пакетного менеджера и лишних утилит; пользователь nonroot (65532).
FROM gcr.io/distroless/python3-debian13:nonroot@sha256:83aa8d4f74a4d7f7cf2d472054139bef71a927b76c680c0f2e1021d6b1d6d732

ARG VERSION=dev
LABEL org.opencontainers.image.title="to-do-board" \
      org.opencontainers.image.description="Канбан-доска для домашних дел" \
      org.opencontainers.image.source="https://github.com/gera-corp/to-do-board" \
      org.opencontainers.image.version="${VERSION}"

# Код принадлежит root и недоступен для записи процессу приложения.
COPY --from=deps --chown=0:0 --chmod=0755 /install /opt/app
# Единственный каталог, доступный приложению на запись (база SQLite).
COPY --from=deps --chown=65532:65532 --chmod=0700 /data /data

ENV PYTHONPATH=/opt/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TODO_DB=/data/tasks.db \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /opt/app
USER 65532:65532
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python3", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/api/health', timeout=2)"]

ENTRYPOINT ["python3", "-m", "app"]
