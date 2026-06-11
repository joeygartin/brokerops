# The reviewer's example: a fully self-contained demo deploy. All three
# integrations run their bundled stubs in-process — zero external credentials.

client_name = "demo"
project_id  = "CHANGE-ME" # set after creating the dedicated GCP project
region      = "us-west1"

api_image      = "us-west1-docker.pkg.dev/CHANGE-ME/brokerops/api:latest"
frontend_image = "us-west1-docker.pkg.dev/CHANGE-ME/brokerops/frontend:latest"

reso_base_url = "internal"
fub_base_url  = "internal"
vapi_base_url = "internal"
