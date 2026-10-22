FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# Unprivileged nginx: runs as user nginx and listens on 8080
FROM nginxinc/nginx-unprivileged:1.29-alpine
COPY --from=build /app/dist /usr/share/nginx/html
RUN cat > /etc/nginx/conf.d/default.conf <<'CONF'
# Uploads are the expensive request: 20 a minute from one address, a short burst allowed
# Only POST (an upload) gets a key; reading the list is not limited
map $request_method $upload_key {
    POST $binary_remote_addr;
    default "";
}
limit_req_zone $upload_key zone=uploads:1m rate=20r/m;

server {
    listen 8080;
    server_name _;
    root /usr/share/nginx/html;
    client_max_body_size 12m;
    server_tokens off;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Referrer-Policy same-origin always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
    # Everything is served from this origin; antd writes its styles inline, so style needs 'unsafe-inline'
    add_header Content-Security-Policy "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'" always;

    # Docker DNS: the backend address is resolved again after the container is recreated
    resolver 127.0.0.11 valid=10s ipv6=off;
    set $backend http://backend:8000;

    # API docs are for the machine of the admin (127.0.0.1:8000), not for the network
    location ~ ^/api/(docs|redoc|openapi\.json) {
        return 404;
    }

    location = /api/jobs {
        limit_req zone=uploads burst=10 nodelay;
        limit_req_status 429;
        proxy_pass $backend;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
    }

    location /api/ {
        proxy_pass $backend;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
    }

    location /assets/ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    location / {
        try_files $uri /index.html;
    }
}
CONF
EXPOSE 8080
