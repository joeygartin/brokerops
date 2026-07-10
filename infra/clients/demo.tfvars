# The reviewer's example: a fully self-contained demo deploy. All three
# integrations run their bundled stubs in-process — zero external credentials.

client_name = "demo"
project_id  = "brokerops-demo" # set after creating the dedicated GCP project
region      = "us-west1"

# The demo tracks `latest` (rolled by Cloud Build on every push to main; ADR-0025).
# A pinned client instead sets a release tag via `make deploy VERSION=vX.Y.Z`.
image_version = "latest"

reso_base_url = "internal"
fub_base_url  = "internal"
vapi_base_url = "internal"

# The public demo showcases the seed/reset button; a real client deploy leaves this
# unset so the data-wiping /demo/* routes stay absent (BOP-007). Not sensitive, so it
# lives in the committed tfvars rather than a -var override.
enable_demo_routes = true
