#!/bin/sh
# Generate env.js with runtime environment variables for Railway.
cat > /usr/share/nginx/html/env.js << EOF
window.__ENV = {
  VITE_CLERK_PUBLISHABLE_KEY: "$VITE_CLERK_PUBLISHABLE_KEY",
  VITE_API_URL: "$VITE_API_URL",
};
EOF

exec nginx -g 'daemon off;'