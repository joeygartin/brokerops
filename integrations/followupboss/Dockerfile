FROM python:3.12-slim

WORKDIR /app
COPY core/ ./core/
COPY integrations/followupboss/ ./integrations/followupboss/
RUN pip install --no-cache-dir ./core ./integrations/followupboss

EXPOSE 8002
CMD ["uvicorn", "brokerops_followupboss.stub:app", "--host", "0.0.0.0", "--port", "8002"]
