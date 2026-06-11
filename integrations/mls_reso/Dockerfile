FROM python:3.12-slim

WORKDIR /app
COPY core/ ./core/
COPY integrations/mls_reso/ ./integrations/mls_reso/
RUN pip install --no-cache-dir ./core ./integrations/mls_reso

EXPOSE 8001
CMD ["uvicorn", "brokerops_mls_reso.server:app", "--host", "0.0.0.0", "--port", "8001"]
