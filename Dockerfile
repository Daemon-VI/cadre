# Cadre engine and dashboard in a container (FR-16.4, ADR-026).
# Built and published in CI (.github/workflows/release.yml); this laptop does not build images.
#
#   docker run -d --name cadre -p 127.0.0.1:8765:8765 -v cadre-data:/data \
#     -e GROQ_API_KEY ghcr.io/daemon-vi/cadre:latest
#   docker exec cadre cadre provider add groq      # uses GROQ_API_KEY from the environment
#   docker exec cadre cadre ui --print             # the dashboard link, token in the fragment
#
# Runs as a non-root user, keeps all state in /data, and reads keys only from environment
# variables (CADRE_NO_KEYRING=1: a container has no OS keychain).

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv build --wheel --out-dir /wheels

FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/Daemon-VI/cadre" \
      org.opencontainers.image.description="Cadre: a team of AI agents on free model keys" \
      org.opencontainers.image.licenses="Apache-2.0"
# git: project mode works in a git worktree of a mounted repository
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 cadre \
    && mkdir /data && chown cadre:cadre /data
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels
ENV CADRE_HOME=/data CADRE_NO_KEYRING=1 PYTHONUNBUFFERED=1
USER cadre
WORKDIR /home/cadre
VOLUME /data
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/v1/health', timeout=4)"
ENTRYPOINT ["cadre"]
# 0.0.0.0 inside the container; publish the port on the host's loopback only (see compose.yaml).
CMD ["serve", "--host", "0.0.0.0"]
