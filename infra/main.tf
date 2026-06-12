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

  client_name       = var.client_name
  project_id        = var.project_id
  region            = var.region
  db_tier           = var.db_tier
  api_image         = var.api_image
  frontend_image    = var.frontend_image
  enable_langsmith  = var.enable_langsmith
  enable_redis      = var.enable_redis
  orchestrator      = var.orchestrator
  vapi_assistant_id = var.vapi_assistant_id
  reso_base_url     = var.reso_base_url
  fub_base_url      = var.fub_base_url
  vapi_base_url     = var.vapi_base_url
  public            = var.public
  cron_schedule     = var.cron_schedule
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
