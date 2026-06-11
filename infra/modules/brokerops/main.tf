# The per-client deployable unit: two Cloud Run services, a Cloud SQL
# database, Secret Manager shells, a milestone-cron Scheduler job, and
# least-privilege service accounts.

data "google_project" "this" {
  project_id = var.project_id
}

locals {
  prefix           = "brokerops-${var.client_name}"
  api_service      = "${local.prefix}-api"
  frontend_service = "${local.prefix}-frontend"

  # Cloud Run deterministic URLs — computable before the services exist,
  # which breaks the frontend-needs-api-URL / CORS-needs-frontend-URL cycle.
  api_url      = "https://${local.api_service}-${data.google_project.this.number}.${var.region}.run.app"
  frontend_url = "https://${local.frontend_service}-${data.google_project.this.number}.${var.region}.run.app"
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
