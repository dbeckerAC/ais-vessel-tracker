FROM node:22-alpine AS frontend-builder

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build \
    && cp node_modules/maplibre-gl/dist/maplibre-gl-worker.mjs \
        dist/assets/maplibre-gl-worker.mjs \
    && cp node_modules/maplibre-gl/dist/maplibre-gl-shared.mjs \
        dist/assets/maplibre-gl-shared.mjs

FROM python:3.12-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/app ./app
COPY config ./config
COPY --from=frontend-builder /frontend/dist ./static

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
