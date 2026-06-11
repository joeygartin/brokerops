# Daily milestone check. The job authenticates with the shared X-Cron-Key
# header (the same CRON_SECRET the api reads). Upgrading to Cloud Run OIDC
# token verification in the api is a documented refinement.

resource "google_cloud_scheduler_job" "milestones" {
  project   = var.project_id
  region    = var.region
  name      = "${local.prefix}-milestones"
  schedule  = var.cron_schedule
  time_zone = "America/Los_Angeles"

  http_target {
    http_method = "POST"
    uri         = "${local.api_url}/internal/cron/milestones"
    headers = {
      "X-Cron-Key" = random_password.cron.result
    }
  }

  retry_config {
    retry_count = 1
  }

  depends_on = [google_cloud_run_v2_service.api]
}
