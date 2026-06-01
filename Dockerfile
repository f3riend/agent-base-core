FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS api-builder

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /opt/api

COPY agent-base-api/pyproject.toml agent-base-api/uv.lock ./
ARG UV_HTTP_TIMEOUT=300
ARG UV_HTTP_RETRIES=5
RUN UV_HTTP_TIMEOUT=${UV_HTTP_TIMEOUT} UV_HTTP_RETRIES=${UV_HTTP_RETRIES} uv sync --no-dev

COPY agent-base-api ./

FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    API_PORT=8000 \
    APP_DIR=/opt/api \
    CELERY_WORKER_CONCURRENCY=4 \
    APP_INTERNAL_API_URL=http://127.0.0.1:8000 \
    APP_BROWSER_API_BASE=/api

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ffmpeg \
        libjpeg62-turbo \
        zlib1g \
        nginx \
        supervisor \
        php8.2-fpm \
        php8.2-cli \
        php8.2-curl \
        php8.2-mbstring \
    && rm -f /etc/nginx/sites-enabled/default /etc/nginx/conf.d/default.conf \
    && printf '\n; agent-base: PHP-FPM ortamini konteyner envden al\nclear_env = no\n' >> /etc/php/8.2/fpm/pool.d/www.conf \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY docker/write-php-prefix-conf.sh docker/layout-php-for-nginx.sh docker/nginx.conf /app/docker/
COPY php-ui /app/php-ui

# Alt dizin: APP_BASE_PATH=/posting/agent-base/ (VITE_BASE_PATH ile ayni)
ARG APP_BASE_PATH=/
ARG VITE_BASE_PATH=/
ARG VITE_API_URL=/api
ENV APP_BASE_PATH=${APP_BASE_PATH} \
    VITE_BASE_PATH=${VITE_BASE_PATH} \
    APP_BROWSER_API_BASE=${VITE_API_URL}

RUN chmod +x /app/docker/write-php-prefix-conf.sh /app/docker/layout-php-for-nginx.sh \
    && /app/docker/write-php-prefix-conf.sh "${APP_BASE_PATH}" \
    && APP_BASE_PATH="${APP_BASE_PATH}" VITE_BASE_PATH="${VITE_BASE_PATH}" \
        /app/docker/layout-php-for-nginx.sh /app/php-ui/public /app/nginx-html \
    && cp -a /app/nginx-html/. /usr/share/nginx/html/

COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

COPY --from=api-builder /opt/api /opt/api

WORKDIR /opt/api

EXPOSE 80

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
