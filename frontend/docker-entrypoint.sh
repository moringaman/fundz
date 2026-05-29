#!/bin/sh
# Generate env.js with runtime environment variables.
cat > /usr/share/nginx/html/env.js << EOF
window.__ENV = {
  VITE_CLERK_PUBLISHABLE_KEY: "$VITE_CLERK_PUBLISHABLE_KEY",
};
EOF

# BACKEND_HOST: Railway public domain (e.g. fundz-api-production.up.railway.app)
# On Railway, the gateway redirects HTTP→HTTPS, so proxy over HTTPS.
# Use empty BACKEND_PORT for public domains, or :8000 for internal DNS.
export BACKEND_PORT="${BACKEND_PORT:-}"
export BACKEND_PROTO="${BACKEND_PROTO:-https}"

envsubst '$BACKEND_HOST $BACKEND_PORT $BACKEND_PROTO' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf
exec nginx -g 'daemon off;'