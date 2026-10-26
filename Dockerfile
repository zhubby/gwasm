FROM node:22.23.4-bookworm-slim AS docs

WORKDIR /src

COPY package.json package-lock.json astro.config.mjs tsconfig.json ./
COPY scripts/ ./scripts/
COPY docs/ ./docs/
COPY images/ ./images/
COPY fonts/ ./fonts/

RUN npm ci --no-audit --no-fund \
    && npm run docs:check \
    && npm run docs:build \
    && test -f docs-dist/index.html \
    && test -f docs-dist/en/index.html \
    && test -f docs-dist/pagefind/wasm.en.pagefind

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system gwasm \
    && adduser --system --ingroup gwasm --no-create-home gwasm \
    && mkdir -p /data/cache /data/dist \
    && chown -R gwasm:gwasm /data

COPY --chown=gwasm:gwasm gwasm.py README.md LICENSE /app/
COPY --chown=gwasm:gwasm gwasm_core/ /app/gwasm_core/
COPY --chown=gwasm:gwasm harness/ /app/harness/
COPY --chown=gwasm:gwasm images/ /app/images/
COPY --chown=gwasm:gwasm fonts/ /app/fonts/
COPY --from=docs /src/docs-dist/ /app/docs-dist/

USER gwasm

VOLUME ["/data"]
EXPOSE 8080

ENTRYPOINT ["python", "/app/gwasm.py"]
CMD ["--no-browser", "--bind", "0.0.0.0", "--port", "8080", "--cache", "/data/cache", "--dir", "/data/dist"]
