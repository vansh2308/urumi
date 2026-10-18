# syntax=docker/dockerfile:1.7
# Dashboard: static Vite build served by unprivileged nginx, which also proxies /api/gateway and
# /api/agent to the services (as the Vite dev server does), so the browser talks to one origin
# and the bundle contains no environment-specific URLs or keys. Build from this directory:
#   docker build -t urumi/frontend:dev .
# Run-time configuration: GATEWAY_UPSTREAM, AGENT_UPSTREAM (absolute http URLs, no path).

FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.28-alpine AS runtime
# Upstreams are resolved per request through the pod's resolver, so nginx starts even when a
# backend is not up yet. Use fully qualified names in Kubernetes (nginx ignores search domains).
ENV GATEWAY_UPSTREAM=http://gateway:8100 \
    AGENT_UPSTREAM=http://agent:8201 \
    NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1 \
    NGINX_ENTRYPOINT_QUIET_LOGS=1
COPY deploy/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz"]
