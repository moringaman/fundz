#!/bin/sh
# Generate env.js with runtime environment variables.
# Vite bakes vars at build time — this injects them at container startup
# so Railway can set VITE_CLERK_PUBLISHABLE_KEY at deploy time.

cat > /usr/share/nginx/html/env.js << EOF
window.__ENV = {
  VITE_CLERK_PUBLISHABLE_KEY: "$VITE_CLERK_PUBLISHABLE_KEY",
};
EOF

# BACKEND_HOST: Railway service's public domain (e.g. fundz-api-production.up.railway.app)
# or internal DNS (e.g. backend.railway.internal). Internal needs BACKEND_PORT=:8000.
export BACKEND_PORT="${BACKEND_PORT:-}"

envsubst '$BACKEND_HOST $BACKEND_PORT' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf
exec nginx -g 'daemon off;'