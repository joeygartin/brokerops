# Secret Manager: the module creates secret *containers* with placeholder
# versions; real values are pushed out-of-band by `make secrets CLIENT=…`.
# Placeholders exist so Cloud Run can reference :latest at first deploy.

locals {
  client_secrets = [
    "fub-api-key",
    "vapi-api-key",
    "vapi-webhook-secret",
    "langsmith-api-key",
    "llm-api-key",
    "reso-auth-token",
    "ses-secret-access-key",
    "smtp-password",
  ]
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
  # The Vapi webhook secret is seeded with a generated value below, not the
  # repo-known "unset" placeholder — the fail-closed webhook must never accept a
  # value anyone can read from the repo. Every other client secret keeps the
  # placeholder until `make secrets` pushes the real value.
  for_each    = { for name, secret in google_secret_manager_secret.client : name => secret if name != "vapi-webhook-secret" }
  secret      = each.value.id
  secret_data = "unset"

  lifecycle {
    # `make secrets` adds real versions; never let terraform revert them.
    ignore_changes = [secret_data]
  }
}

# The Vapi webhook shared secret. Terraform seeds a generated value (never the
# repo-known "unset") so the in-process demo stub authenticates against the
# fail-closed webhook with zero manual push. A real Vapi client overwrites it via
# `make secrets` with the value configured in the Vapi dashboard — a newer version
# wins, and ignore_changes stops terraform reverting it.
resource "random_password" "vapi_webhook_secret" {
  length  = 32
  special = false
}

resource "google_secret_manager_secret_version" "vapi_webhook_secret" {
  secret      = google_secret_manager_secret.client["vapi-webhook-secret"].id
  secret_data = random_password.vapi_webhook_secret.result

  lifecycle {
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

# The session-token signing key (ADR-0008). Like the cron secret, terraform
# generates it — no `make secrets` push — so enabling magic link needs no manual
# key handling.
resource "random_password" "session_signing_key" {
  length  = 48
  special = false
}

resource "google_secret_manager_secret" "session_signing_key" {
  project   = var.project_id
  secret_id = "${local.prefix}-session-signing-key"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "session_signing_key" {
  secret      = google_secret_manager_secret.session_signing_key.id
  secret_data = random_password.session_signing_key.result
}

locals {
  api_secret_ids = merge(
    { for name, secret in google_secret_manager_secret.client : name => secret.secret_id },
    {
      "database-url"        = google_secret_manager_secret.database_url.secret_id
      "cron-secret"         = google_secret_manager_secret.cron_secret.secret_id
      "session-signing-key" = google_secret_manager_secret.session_signing_key.secret_id
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
