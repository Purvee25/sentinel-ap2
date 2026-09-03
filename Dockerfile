FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY fuzz_report.json .

RUN useradd -m sentinel && mkdir -p /app/data && chown -R sentinel:sentinel /app
USER sentinel

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
