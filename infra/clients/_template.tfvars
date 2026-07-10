# Copy to <client>.tfvars and fill in. NO SECRETS here — these files are
# committed; keys go to Secret Manager via `make secrets CLIENT=<client>`.

client_name = "acme"
project_id  = "your-gcp-project-id"
region      = "us-west1"

# Images are pinned by version at deploy time, not here (ADR-0025):
#   make deploy CLIENT=acme VERSION=vX.Y.Z
# The full registry refs are derived from project_id + region + the version tag.
# For a throwaway working-tree build use `make deploy-dev CLIENT=acme` (pins :latest).

# Real client integrations (drop these lines to run the bundled stubs).
# Keys go to Secret Manager via `make secrets` — never here.
# reso_base_url = "https://reso.your-mls.example/odata"   # + reso-auth-token secret
# fub_base_url  = "https://api.followupboss.com/v1"
# vapi_base_url = "https://api.vapi.ai"
# vapi_assistant_id = "your-vapi-assistant-id"

# enable_langsmith = true
