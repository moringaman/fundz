#!/bin/sh
# Generate env.js with runtime environment variables.
# Vite bakes vars at build time — this injects them at container startup
# so Railway can set VITE_CLERK_PUBLISHABLE_KEY at deploy time.

cat > /usr/share/nginx/html/env.js << EOF
window.__ENV = {
  VITE_CLERK_PUBLISHABLE_KEY: "$VITE_CLERK_PUBLISHABLE_KEY",
};
EOF

envsubst '$BACKEND_HOST' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf
exec nginx -g 'daemon off;'