FROM debian:13.4

ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/hermes/.playwright
ENV npm_config_install_links=false

RUN rm -f /etc/apt/sources.list.d/debian.sources && \
    printf '%s\n' \
    'deb http://mirrors.aliyun.com/debian trixie main contrib non-free non-free-firmware' \
    'deb http://mirrors.aliyun.com/debian trixie-updates main contrib non-free non-free-firmware' \
    'deb http://mirrors.aliyun.com/debian-security trixie-security main contrib non-free non-free-firmware' \
    > /etc/apt/sources.list && \
    printf 'Acquire::ForceIPv4 "true";\nAcquire::Retries "2";\nAcquire::http::Timeout "30";\n' > /etc/apt/apt.conf.d/99fuxi-timeouts && \
    apt-get -o=Dpkg::Use-Pty=0 update && \
    apt-get install -y --no-install-recommends \
    build-essential curl nodejs npm python3 ripgrep ffmpeg gcc python3-dev libffi-dev procps git openssh-client docker-cli tini gosu && \
    rm -rf /var/lib/apt/lists/*

RUN useradd -u 10000 -m -d /opt/data hermes
COPY .fuxi-build-tools/uv .fuxi-build-tools/uvx /usr/local/bin/

WORKDIR /opt/hermes

COPY package.json package-lock.json ./
COPY web/package.json web/package-lock.json web/
COPY ui-tui/package.json ui-tui/package-lock.json ui-tui/
COPY ui-tui/packages/hermes-ink/ ui-tui/packages/hermes-ink/

RUN npm install --prefer-offline --no-audit --ignore-scripts && \
    npx playwright install --with-deps chromium --only-shell && \
    (cd web && npm install --prefer-offline --no-audit) && \
    (cd ui-tui && npm install --prefer-offline --no-audit) && \
    npm cache clean --force

COPY pyproject.toml uv.lock ./
RUN touch ./README.md
RUN UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    UV_HTTP_TIMEOUT=120 \
    UV_CONCURRENT_DOWNLOADS=1 \
    uv sync --frozen --no-install-project --extra all

COPY --chown=hermes:hermes . .

RUN cd web && npm run build && \
    cd ../ui-tui && npm run build

USER root
RUN chmod -R a+rX /opt/hermes && \
    chown -R hermes:hermes /opt/hermes/.venv /opt/hermes/ui-tui /opt/hermes/node_modules
RUN uv pip install --no-cache-dir --no-deps -e "."

ENV HERMES_WEB_DIST=/opt/hermes/hermes_cli/web_dist
ENV HERMES_HOME=/opt/data
ENV PATH="/opt/data/.local/bin:${PATH}"
VOLUME [ "/opt/data" ]
ENTRYPOINT [ "/usr/bin/tini", "-g", "--", "/opt/hermes/docker/entrypoint.sh" ]
