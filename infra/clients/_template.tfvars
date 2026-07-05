# Copy to <client>.tfvars and fill in. NO SECRETS here — these files are
# committed; keys go to Secret Manager via `make secrets CLIENT=<client>`.

client_name = "acme"
project_id  = "your-gcp-project-id"
region      = "us-west1"

# Built + pushed by `make gcp-images CLIENT=acme`
api_image      = "us-west1-docker.pkg.dev/your-gcp-project-id/brokerops/api:latest"
frontend_image = "us-west1-docker.pkg.dev/your-gcp-project-id/brokerops/frontend:latest"

# Real client integrations (drop these lines to run the bundled stubs).
# Keys go to Secret Manager via `make secrets` — never here.
# reso_base_url = "https://reso.your-mls.example/odata"   # + reso-auth-token secret
# fub_base_url  = "https://api.followupboss.com/v1"
# vapi_base_url = "https://api.vapi.ai"
# vapi_assistant_id = "your-vapi-assistant-id"

# enable_langsmith = true
