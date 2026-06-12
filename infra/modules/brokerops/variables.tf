variable "client_name" {
  description = "Short client slug — names every resource (e.g. \"demo\", \"acme\")."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,18}$", var.client_name))
    error_message = "client_name must be a short lowercase slug (letters, digits, hyphens)."
  }
}

variable "project_id" {
  description = "GCP project this client deploys into."
  type        = string
}

variable "region" {
  description = "GCP region."
  type        = string
  default     = "us-west1"
}

variable "db_tier" {
  description = "Cloud SQL machine tier; db-f1-micro is fine for pilots."
  type        = string
  default     = "db-f1-micro"
}

variable "api_image" {
  description = "Artifact Registry image ref for the api service."
  type        = string
}

variable "frontend_image" {
  description = "Artifact Registry image ref for the frontend service."
  type        = string
}

variable "enable_langsmith" {
  description = "Inject the LangSmith tracing key + enable tracing env."
  type        = bool
  default     = false
}

variable "enable_redis" {
  description = "Memorystore + VPC connector for the RedisCache backend. Not implemented in V1 — the in-memory cache backend is the default; revisit per ADR-0001."
  type        = bool
  default     = false
  validation {
    condition     = var.enable_redis == false
    error_message = "enable_redis is reserved: the Memorystore backend is not implemented in the V1 module (see ADR-0001 revisit triggers)."
  }
}

variable "vapi_assistant_id" {
  description = "Vapi assistant id used for outbound feedback calls."
  type        = string
  default     = "demo-assistant"
}

variable "orchestrator" {
  description = "Workflow engine: langgraph (default) or adk — same workflows, approvals, and API surface either way (ADR-0004)."
  type        = string
  default     = "langgraph"
  validation {
    condition     = contains(["langgraph", "adk"], var.orchestrator)
    error_message = "orchestrator must be \"langgraph\" or \"adk\"."
  }
}

# Integration endpoints. The sentinel "internal" runs that integration's stub
# in-process inside the api container — a demo client deploys with zero
# external credentials. Real clients point these at the live APIs.
variable "reso_base_url" {
  description = "RESO Web API base URL, or \"internal\" for the bundled mock."
  type        = string
  default     = "internal"
}

variable "fub_base_url" {
  description = "FollowUpBoss API base URL, or \"internal\" for the stub."
  type        = string
  default     = "internal"
}

variable "vapi_base_url" {
  description = "Vapi API base URL, or \"internal\" for the stub."
  type        = string
  default     = "internal"
}

variable "public" {
  description = "Allow unauthenticated access to both services (demo). Real clients front this with IAP/auth."
  type        = bool
  default     = true
}

variable "cron_schedule" {
  description = "Cloud Scheduler cron for the milestone check."
  type        = string
  default     = "0 13 * * *"
}
