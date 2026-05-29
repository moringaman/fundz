#!/bin/sh
# Generate env.js with runtime environment variables for Railway.
cat > /usr/share/nginx/html/env.js << EOF
window.__ENV = {
  VITE_CLERK_PUBLISHABLE_KEY: "$VITE_CLERK_PUBLISHABLE_KEY",
  VITE_API_URL: "$VITE_API_URL",
};
EOF

# BACKEND_HOST etc. only needed if using nginx proxy (currently unused)
export BACKEND_PORT="${BACKEND_PORT:-}"
export BACKEND_PROTO="${BACKEND_PROTO:-https}"

envsubst '$BACKEND_HOST $BACKEND_PORT $BACKEND_PROTO' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf
exec nginx -g 'daemon off;'