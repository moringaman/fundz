FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template

# BACKEND_HOST: set to 'phemex-ai-trader-backend' (docker-compose)
# or 'backend.railway.internal' (Railway) via environment variable
ENV BACKEND_HOST=phemex-ai-trader-backend

EXPOSE 3000

# Runtime entrypoint: inject env vars into env.js (for Clerk publishable key, etc.)
# then start nginx. Vite vars are baked at build time — this allows Railway
# to inject VITE_CLERK_PUBLISHABLE_KEY at deploy time instead.
CMD ["/bin/sh", "-c", "\
  echo \"window.__ENV = {\" > /usr/share/nginx/html/env.js && \
  echo \"  VITE_CLERK_PUBLISHABLE_KEY: '${VITE_CLERK_PUBLISHABLE_KEY}'\",\" >> /usr/share/nginx/html/env.js && \
  echo \"};\" >> /usr/share/nginx/html/env.js && \
  envsubst '${BACKEND_HOST}' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf && \
  exec nginx -g 'daemon off;'"]