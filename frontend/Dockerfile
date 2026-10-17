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
server {
    listen 8080;
    server_name _;
    root /usr/share/nginx/html;
    client_max_body_size 12m;
    server_tokens off;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Referrer-Policy same-origin always;

    location /api/ {
        proxy_pass http://backend:8000;
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
