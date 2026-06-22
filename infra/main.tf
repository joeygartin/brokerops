# Root config: one state per client (backend prefix set by `make deploy`),
# instantiating the per-client module with that client's tfvars.

terraform {
  required_version = ">= 1.7"

  backend "gcs" {} # bucket + prefix supplied via -backend-config (see Makefile)

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

module "client" {
  source = "./modules/brokerops"

  client_name           = var.client_name
  project_id            = var.project_id
  region                = var.region
  db_tier               = var.db_tier
  api_image             = var.api_image
  frontend_image        = var.frontend_image
  enable_langsmith      = var.enable_langsmith
  enable_llm_extraction = var.enable_llm_extraction
  llm_model             = var.llm_model
  enable_redis          = var.enable_redis
  orchestrator          = var.orchestrator
  vapi_assistant_id     = var.vapi_assistant_id
  reso_base_url         = var.reso_base_url
  fub_base_url          = var.fub_base_url
  vapi_base_url         = var.vapi_base_url
  public                = var.public
  enable_auth           = var.enable_auth
  google_oidc_client_id = var.google_oidc_client_id
  auth_allowed_domain   = var.auth_allowed_domain
  auth_allowed_emails   = var.auth_allowed_emails
  auth_methods          = var.auth_methods
  auth_admin_emails     = var.auth_admin_emails
  auth_admin_domain     = var.auth_admin_domain
  auth_viewer_emails    = var.auth_viewer_emails
  auth_viewer_domain    = var.auth_viewer_domain
  public_base_url       = var.public_base_url
  smtp_host             = var.smtp_host
  smtp_port             = var.smtp_port
  smtp_from             = var.smtp_from
  smtp_username         = var.smtp_username
  cron_schedule         = var.cron_schedule
}

output "api_url" {
  value = module.client.api_url
}

output "frontend_url" {
  value = module.client.frontend_url
}

output "sql_connection_name" {
  value = module.client.sql_connection_name
}

output "secret_ids" {
  value = module.client.secret_ids
}
