FROM python:3.12-slim

WORKDIR /app
COPY core/ ./core/
COPY integrations/vapi/ ./integrations/vapi/
RUN pip install --no-cache-dir ./core ./integrations/vapi

EXPOSE 8003
CMD ["uvicorn", "brokerops_vapi.stub:app", "--host", "0.0.0.0", "--port", "8003"]
