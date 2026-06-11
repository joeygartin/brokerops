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
        name  = "RESO_BASE_URL"
        value = var.reso_base_url
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
    }
  }

  depends_on = [
    google_secret_manager_secret_version.client_placeholder,
    google_secret_manager_secret_version.database_url,
    google_secret_manager_secret_version.cron_secret,
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
