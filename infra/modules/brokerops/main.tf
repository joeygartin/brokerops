# The per-client deployable unit: two Cloud Run services, a Cloud SQL
# database, Secret Manager shells, a milestone-cron Scheduler job, and
# least-privilege service accounts.
#
# URL wiring: the frontend nginx proxies /api/* to the api service
# (API_UPSTREAM env, injected from the api's real .uri after creation), so
# the browser only ever talks same-origin — no URL baked into the bundle,
# no CORS coupling, no predicted-URL fragility.

locals {
  prefix           = "brokerops-${var.client_name}"
  api_service      = "${local.prefix}-api"
  frontend_service = "${local.prefix}-frontend"
}

resource "google_service_account" "api" {
  project      = var.project_id
  account_id   = "${local.prefix}-api"
  display_name = "brokerops ${var.client_name} api"
}

resource "google_service_account" "scheduler" {
  project      = var.project_id
  account_id   = "${local.prefix}-cron"
  display_name = "brokerops ${var.client_name} scheduler"
}

resource "google_project_iam_member" "api_cloudsql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.api.email}"
}
