FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LINEAFORGE_HOST=0.0.0.0 LINEAFORGE_DEPLOYMENT=1 LINEAFORGE_DATA_DIR=/data
WORKDIR /app
COPY requirements-web.txt ./
RUN pip install --no-cache-dir -r requirements-web.txt && useradd --uid 10001 --create-home lineaforge && mkdir /data && chown lineaforge /data
COPY lineaforge/ ./lineaforge/
COPY --from=frontend /build/dist/ ./web/dist/
USER lineaforge
EXPOSE 8765
VOLUME ["/data"]
CMD ["python", "-m", "lineaforge.web_launcher", "--host", "0.0.0.0", "--port", "8765"]
