FROM python:3.12-slim

WORKDIR /app
COPY core/ ./core/
COPY integrations/mls_reso/ ./integrations/mls_reso/
COPY integrations/followupboss/ ./integrations/followupboss/
COPY integrations/google_drive/ ./integrations/google_drive/
COPY integrations/google_oidc/ ./integrations/google_oidc/
COPY integrations/email_smtp/ ./integrations/email_smtp/
COPY integrations/email_stub/ ./integrations/email_stub/
COPY integrations/llm_extraction/ ./integrations/llm_extraction/
COPY integrations/pydantic_ai_extraction/ ./integrations/pydantic_ai_extraction/
COPY integrations/vapi/ ./integrations/vapi/
COPY orchestration/langgraph/ ./orchestration/langgraph/
COPY orchestration/adk/ ./orchestration/adk/
COPY api/ ./api/
RUN pip install --no-cache-dir ./core ./integrations/mls_reso ./integrations/followupboss \
<<<<<<< HEAD
    ./integrations/google_oidc ./integrations/email_smtp ./integrations/email_stub \
    ./integrations/llm_extraction \
    ./integrations/pydantic_ai_extraction ./integrations/vapi \
=======
    ./integrations/google_drive ./integrations/google_oidc ./integrations/email_smtp \
    ./integrations/llm_extraction ./integrations/pydantic_ai_extraction ./integrations/vapi \
>>>>>>> c19d7c4 (feat: BOP-021 FilesPort + Google Drive adapter + documents table + transaction Documents tab)
    ./orchestration/langgraph ./orchestration/adk ./api

EXPOSE 8000
CMD ["sh", "-c", "alembic -c api/alembic.ini upgrade head && uvicorn brokerops_api.main:app --host 0.0.0.0 --port 8000"]
