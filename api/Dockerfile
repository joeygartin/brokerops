FROM python:3.12-slim

WORKDIR /app
COPY core/ ./core/
COPY integrations/mls_reso/ ./integrations/mls_reso/
COPY integrations/followupboss/ ./integrations/followupboss/
COPY integrations/llm_extraction/ ./integrations/llm_extraction/
COPY integrations/vapi/ ./integrations/vapi/
COPY orchestration/langgraph/ ./orchestration/langgraph/
COPY orchestration/adk/ ./orchestration/adk/
COPY api/ ./api/
RUN pip install --no-cache-dir ./core ./integrations/mls_reso ./integrations/followupboss \
    ./integrations/llm_extraction ./integrations/vapi ./orchestration/langgraph \
    ./orchestration/adk ./api

EXPOSE 8000
CMD ["sh", "-c", "alembic -c api/alembic.ini upgrade head && uvicorn brokerops_api.main:app --host 0.0.0.0 --port 8000"]
