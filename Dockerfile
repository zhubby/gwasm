FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system gw \
    && adduser --system --ingroup gw --no-create-home gw \
    && mkdir -p /data/cache /data/dist \
    && chown -R gw:gw /data

COPY --chown=gw:gw gw.py README.md LICENSE /app/
COPY --chown=gw:gw harness/ /app/harness/
COPY --chown=gw:gw images/ /app/images/
COPY --chown=gw:gw fonts/ /app/fonts/

USER gw

VOLUME ["/data"]
EXPOSE 8080

ENTRYPOINT ["python", "/app/gw.py"]
CMD ["--no-browser", "--bind", "0.0.0.0", "--port", "8080", "--cache", "/data/cache", "--dir", "/data/dist"]
