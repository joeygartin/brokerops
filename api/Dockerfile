FROM python:3.12-slim

WORKDIR /app
COPY api/ ./api/
RUN pip install --no-cache-dir ./api

EXPOSE 8000
CMD ["uvicorn", "brokerops_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
