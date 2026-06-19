variable "client_name" {
  type = string
}

variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "us-west1"
}

variable "db_tier" {
  type    = string
  default = "db-f1-micro"
}

variable "api_image" {
  type = string
}

variable "frontend_image" {
  type = string
}

variable "enable_langsmith" {
  type    = bool
  default = false
}

variable "enable_llm_extraction" {
  description = "Use the Claude feedback extractor (ADR-0006) instead of the deterministic default. Push the real key with `make secrets`."
  type        = bool
  default     = false
}

variable "llm_model" {
  description = "Claude model id for feedback extraction when enable_llm_extraction is true."
  type        = string
  default     = "claude-sonnet-4-6"
}

variable "enable_redis" {
  type    = bool
  default = false
}

variable "vapi_assistant_id" {
  type    = string
  default = "demo-assistant"
}

variable "orchestrator" {
  type    = string
  default = "langgraph"
}

variable "reso_base_url" {
  type    = string
  default = "internal"
}

variable "fub_base_url" {
  type    = string
  default = "internal"
}

variable "vapi_base_url" {
  type    = string
  default = "internal"
}

variable "public" {
  type    = bool
  default = true
}

variable "enable_auth" {
  type    = bool
  default = false
}

variable "google_oidc_client_id" {
  type    = string
  default = "unset"
}

variable "auth_allowed_domain" {
  type    = string
  default = ""
}

variable "auth_allowed_emails" {
  type    = string
  default = ""
}

variable "auth_methods" {
  type    = string
  default = ""
}

variable "public_base_url" {
  type    = string
  default = ""
}

variable "smtp_host" {
  type    = string
  default = ""
}

variable "smtp_port" {
  type    = number
  default = 587
}

variable "smtp_from" {
  type    = string
  default = "no-reply@brokerops.app"
}

variable "smtp_username" {
  type    = string
  default = ""
}

variable "cron_schedule" {
  type    = string
  default = "0 13 * * *"
}
