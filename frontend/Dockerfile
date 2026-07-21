# syntax=docker/dockerfile:1.7
#
# Existing implementation (MVP): the frontend "production" image ran `npm run dev`
# (the Vite DEV SERVER) as its CMD.
#
# Why insufficient / production risk: the Vite dev server is single-connection,
# unoptimised, ships source maps and the full module graph, has no caching or
# compression, and is explicitly documented as not for production. It is also a
# live filesystem watcher. Serving it to users is both a performance and a security
# problem.
#
# Enterprise solution: a multi-stage build. Node compiles the app to optimised,
# hashed, minified static assets; those are served by nginx (unprivileged/non-root
# image) with gzip, long-lived asset caching, SPA history fallback, and security
# headers. The API base URL is injected at BUILD time via VITE_API_BASE.

# ---------- build ----------
FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ARG VITE_API_BASE=/api/v1
ENV VITE_API_BASE=${VITE_API_BASE}
# Empty by default - src/lib/basemap.js falls back to keyless Esri World
# Imagery when these are unset. Set both to swap in Mapbox/Google/etc once a
# key is available; no code change needed.
ARG VITE_BASEMAP_URL=
ARG VITE_BASEMAP_ATTRIBUTION=
ENV VITE_BASEMAP_URL=${VITE_BASEMAP_URL}
ENV VITE_BASEMAP_ATTRIBUTION=${VITE_BASEMAP_ATTRIBUTION}
RUN npm run build

# ---------- serve ----------
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
# Unprivileged image already runs as uid 101 and listens on 8080.
COPY --chown=nginx:nginx nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build --chown=nginx:nginx /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null 2>&1 || exit 1
