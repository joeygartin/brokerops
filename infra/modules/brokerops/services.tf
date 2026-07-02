locals {
  # "magic" present in the comma-separated auth_methods (whitespace-tolerant).
  magic_enabled = contains([for m in split(",", var.auth_methods) : trimspace(m)], "magic")
}

resource "google_cloud_run_v2_service" "api" {
  project  = var.project_id
  name     = local.api_service
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.api.email

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.this.connection_name]
      }
    }

    containers {
      image = var.api_image

      ports {
        container_port = 8000
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      env {
        name  = "ORCHESTRATOR"
        value = var.orchestrator
      }
      # The deploy's tenant identity (BOP-006). Each client deploy binds its own
      # client_name as the tenant below the agent, so a turned agent's blast radius
      # is this one client — never the fleet. Without this a client deploy would run
      # as the "demo" default.
      env {
        name  = "TENANT_ID"
        value = var.client_name
      }
      env {
        name  = "RESO_BASE_URL"
        value = var.reso_base_url
      }
      dynamic "env" {
        # Live RESO endpoints authorize with a bearer token; the bundled mock
        # needs none, so demo deploys don't reference the secret at all.
        for_each = var.reso_base_url == "internal" ? [] : [1]
        content {
          name = "RESO_AUTH_TOKEN"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.client["reso-auth-token"].secret_id
              version = "latest"
            }
          }
        }
      }
      env {
        name  = "FUB_BASE_URL"
        value = var.fub_base_url
      }
      env {
        name  = "VAPI_BASE_URL"
        value = var.vapi_base_url
      }
      env {
        name  = "VAPI_ASSISTANT_ID"
        value = var.vapi_assistant_id
      }
      dynamic "env" {
        # Only the in-process vapi stub consumes this: it fires its
        # end-of-call webhook back at this same container. Real Vapi is
        # configured with the public /webhooks/vapi URL in its dashboard.
        for_each = var.vapi_base_url == "internal" ? [1] : []
        content {
          name  = "WEBHOOK_URL"
          value = "http://localhost:8000/webhooks/vapi"
        }
      }
      env {
        name  = "LANGCHAIN_TRACING_V2"
        value = var.enable_langsmith ? "true" : "false"
      }
      # Demo seed/reset routes are off unless this deploy is the demo (the route can
      # wipe tenant data). Absent → the /demo/* routes 404.
      env {
        name  = "ENABLE_DEMO_ROUTES"
        value = var.enable_demo_routes ? "true" : "false"
      }

      # Operator auth (ADR-0007). Off → no auth env is set, so the api falls
      # back to the demo verifier and the deploy stays login-free. The client
      # id is public (it rides in the browser flow), so these are plain env,
      # not secrets.
      dynamic "env" {
        for_each = var.enable_auth ? [1] : []
        content {
          name  = "GOOGLE_OIDC_CLIENT_ID"
          value = var.google_oidc_client_id
        }
      }
      dynamic "env" {
        for_each = var.enable_auth && var.auth_allowed_domain != "" ? [1] : []
        content {
          name  = "AUTH_ALLOWED_DOMAIN"
          value = var.auth_allowed_domain
        }
      }
      dynamic "env" {
        for_each = var.enable_auth && var.auth_allowed_emails != "" ? [1] : []
        content {
          name  = "AUTH_ALLOWED_EMAILS"
          value = var.auth_allowed_emails
        }
      }

      # RBAC (ADR-0009): role assignment among allowlisted operators. Absent →
      # every operator is admin (pre-RBAC flat behavior).
      dynamic "env" {
        for_each = var.enable_auth && var.auth_admin_emails != "" ? [1] : []
        content {
          name  = "AUTH_ADMIN_EMAILS"
          value = var.auth_admin_emails
        }
      }
      dynamic "env" {
        for_each = var.enable_auth && var.auth_admin_domain != "" ? [1] : []
        content {
          name  = "AUTH_ADMIN_DOMAIN"
          value = var.auth_admin_domain
        }
      }
      dynamic "env" {
        for_each = var.enable_auth && var.auth_viewer_emails != "" ? [1] : []
        content {
          name  = "AUTH_VIEWER_EMAILS"
          value = var.auth_viewer_emails
        }
      }
      dynamic "env" {
        for_each = var.enable_auth && var.auth_viewer_domain != "" ? [1] : []
        content {
          name  = "AUTH_VIEWER_DOMAIN"
          value = var.auth_viewer_domain
        }
      }

      # Multi-method auth (ADR-0008): AUTH_METHODS selects google/magic; magic
      # additionally needs a session signing key, the public frontend URL for
      # email links, and (for real delivery) SMTP. The signing key is terraform-
      # generated; SMTP password is the only pushed secret here.
      dynamic "env" {
        for_each = var.auth_methods != "" ? [1] : []
        content {
          name  = "AUTH_METHODS"
          value = var.auth_methods
        }
      }
      dynamic "env" {
        for_each = local.magic_enabled ? [1] : []
        content {
          name = "SESSION_SIGNING_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.session_signing_key.secret_id
              version = "latest"
            }
          }
        }
      }
      dynamic "env" {
        for_each = local.magic_enabled ? [1] : []
        content {
          name  = "PUBLIC_BASE_URL"
          value = var.public_base_url
        }
      }
      dynamic "env" {
        for_each = local.magic_enabled && var.smtp_host != "" ? [1] : []
        content {
          name  = "SMTP_HOST"
          value = var.smtp_host
        }
      }
      dynamic "env" {
        for_each = local.magic_enabled && var.smtp_host != "" ? [1] : []
        content {
          name  = "SMTP_PORT"
          value = tostring(var.smtp_port)
        }
      }
      dynamic "env" {
        for_each = local.magic_enabled && var.smtp_host != "" ? [1] : []
        content {
          name  = "SMTP_FROM"
          value = var.smtp_from
        }
      }
      dynamic "env" {
        for_each = local.magic_enabled && var.smtp_username != "" ? [1] : []
        content {
          name  = "SMTP_USERNAME"
          value = var.smtp_username
        }
      }
      dynamic "env" {
        for_each = local.magic_enabled && var.smtp_host != "" ? [1] : []
        content {
          name = "SMTP_PASSWORD"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.client["smtp-password"].secret_id
              version = "latest"
            }
          }
        }
      }

      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "CRON_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.cron_secret.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "FUB_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.client["fub-api-key"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "VAPI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.client["vapi-api-key"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "VAPI_WEBHOOK_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.client["vapi-webhook-secret"].secret_id
            version = "latest"
          }
        }
      }

      dynamic "env" {
        for_each = var.enable_langsmith ? [1] : []
        content {
          name = "LANGCHAIN_API_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.client["langsmith-api-key"].secret_id
              version = "latest"
            }
          }
        }
      }

      # LLM feedback extraction (ADR-0006/0014). Off → no EXTRACTION_BACKEND and
      # no LLM secret referenced, so a key-less deploy runs the deterministic
      # extractor. On → the backend is named explicitly (the app never infers it
      # from key presence) and the key secret is injected alongside.
      dynamic "env" {
        for_each = var.enable_llm_extraction ? [1] : []
        content {
          name  = "EXTRACTION_BACKEND"
          value = var.extraction_backend
        }
      }
      dynamic "env" {
        for_each = var.enable_llm_extraction ? [1] : []
        content {
          name = "LLM_API_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.client["llm-api-key"].secret_id
              version = "latest"
            }
          }
        }
      }
      dynamic "env" {
        for_each = var.enable_llm_extraction && var.llm_model != "" ? [1] : []
        content {
          name  = "LLM_MODEL"
          value = var.llm_model
        }
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_version.client_placeholder,
    google_secret_manager_secret_version.vapi_webhook_secret,
    google_secret_manager_secret_version.database_url,
    google_secret_manager_secret_version.cron_secret,
    google_secret_manager_secret_version.session_signing_key,
    google_secret_manager_secret_iam_member.api_access,
  ]
}

resource "google_cloud_run_v2_service" "frontend" {
  project  = var.project_id
  name     = local.frontend_service
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      image = var.frontend_image

      ports {
        container_port = 8080
      }

      env {
        name  = "API_UPSTREAM"
        value = google_cloud_run_v2_service.api.uri
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "api_public" {
  count    = var.public ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "frontend_public" {
  count    = var.public ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.frontend.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
