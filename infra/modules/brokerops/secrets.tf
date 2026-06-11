# Secret Manager: the module creates secret *containers* with placeholder
# versions; real values are pushed out-of-band by `make secrets CLIENT=…`.
# Placeholders exist so Cloud Run can reference :latest at first deploy.

locals {
  client_secrets = ["fub-api-key", "vapi-api-key", "vapi-webhook-secret", "langsmith-api-key"]
}

resource "google_secret_manager_secret" "client" {
  for_each  = toset(local.client_secrets)
  project   = var.project_id
  secret_id = "${local.prefix}-${each.key}"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "client_placeholder" {
  for_each    = google_secret_manager_secret.client
  secret      = each.value.id
  secret_data = "unset"

  lifecycle {
    # `make secrets` adds real versions; never let terraform revert them.
    ignore_changes = [secret_data]
  }
}

# Infra-generated secrets get real values from terraform itself.
resource "google_secret_manager_secret" "database_url" {
  project   = var.project_id
  secret_id = "${local.prefix}-database-url"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "database_url" {
  secret      = google_secret_manager_secret.database_url.id
  secret_data = local.database_url
}

resource "random_password" "cron" {
  length  = 32
  special = false
}

resource "google_secret_manager_secret" "cron_secret" {
  project   = var.project_id
  secret_id = "${local.prefix}-cron-secret"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "cron_secret" {
  secret      = google_secret_manager_secret.cron_secret.id
  secret_data = random_password.cron.result
}

locals {
  api_secret_ids = merge(
    { for name, secret in google_secret_manager_secret.client : name => secret.secret_id },
    {
      "database-url" = google_secret_manager_secret.database_url.secret_id
      "cron-secret"  = google_secret_manager_secret.cron_secret.secret_id
    }
  )
}

resource "google_secret_manager_secret_iam_member" "api_access" {
  for_each  = local.api_secret_ids
  project   = var.project_id
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}
