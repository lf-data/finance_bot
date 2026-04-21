FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir setuptools

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir \
    opentelemetry-distro \
    opentelemetry-exporter-otlp-proto-grpc \
    opentelemetry-instrumentation-flask \
    opentelemetry-instrumentation-psycopg2 \
    opentelemetry-instrumentation-requests \
    opentelemetry-instrumentation-urllib3 \
    opentelemetry-instrumentation-httpx \
    opentelemetry-instrumentation-threading \
    opentelemetry-instrumentation-logging

COPY . .

EXPOSE 5001

CMD ["opentelemetry-instrument", "python", "app.py"]