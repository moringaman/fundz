#!/bin/sh
# Generate env.js with runtime environment variables for Railway.
cat > /usr/share/nginx/html/env.js << EOF
window.__ENV = {
  VITE_CLERK_PUBLISHABLE_KEY: "$VITE_CLERK_PUBLISHABLE_KEY",
  VITE_API_URL: "$VITE_API_URL",
};
EOF

export BACKEND_PORT="${BACKEND_PORT:-}"
export BACKEND_PROTO="${BACKEND_PROTO:-https}"

# Substitute placeholders (double-underscore delimited to avoid conflicting with
# nginx $variables) so that proxy_pass uses a literal URL — no resolver needed.
sed -e "s|__BACKEND_PROTO__|${BACKEND_PROTO}|g"      \
    -e "s|__BACKEND_HOST__|${BACKEND_HOST}|g"         \
    -e "s|__BACKEND_PORT__|${BACKEND_PORT}|g"         \
    /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf
exec nginx -g 'daemon off;'