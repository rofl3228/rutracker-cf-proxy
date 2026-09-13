FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml ./
COPY src ./src
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CLEARANCE_FILE=/data/clearance.json
COPY --from=build /wheels /wheels
# tzdata: so TZ=<Area/City> gives local timestamps in logs
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels \
    && useradd --system --uid 568 --home-dir /data appuser \
    && mkdir -p /data && chown appuser /data
USER appuser
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8080'), timeout=4)"
ENTRYPOINT ["python", "-m", "rutracker_proxy"]
