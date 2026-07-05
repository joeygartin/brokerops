FROM python:3.12-slim

WORKDIR /app
COPY core/ ./core/
COPY integrations/google_drive/ ./integrations/google_drive/
RUN pip install --no-cache-dir ./core ./integrations/google_drive

EXPOSE 8004
CMD ["uvicorn", "brokerops_google_drive.stub:app", "--host", "0.0.0.0", "--port", "8004"]
