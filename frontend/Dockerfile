FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# BACKEND_HOST: set to 'phemex-ai-trader-backend' (docker-compose)
# or 'backend.railway.internal' (Railway) via environment variable
ENV BACKEND_HOST=phemex-ai-trader-backend

EXPOSE 3000

ENTRYPOINT ["/docker-entrypoint.sh"]