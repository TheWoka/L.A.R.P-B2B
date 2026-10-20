# --- Сборка ---
# Контекст сборки — корень репозитория (docker-compose.yml: context: .),
# чтобы в образ попали тексты законов для окна «Норма закона».
FROM node:20-slim AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ .
# legalArticles.ts импортирует ../../../data/legal/*.md → из /app/src/components это /data/legal
COPY data/legal /data/legal
RUN npm run build

# --- Раздача статики ---
FROM nginx:alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
