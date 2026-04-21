FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir \
    opentelemetry-distro \
    opentelemetry-exporter-otlp-proto-grpc
RUN opentelemetry-bootstrap -a install

COPY . .

EXPOSE 5001

# Verifica che il comando esista prima di avviare
CMD ["opentelemetry-instrument", "python", "app.py"]