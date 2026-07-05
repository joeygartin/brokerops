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
  description = "Use an LLM feedback extractor (ADR-0006/0014) instead of the deterministic default. Push the real key with `make secrets`."
  type        = bool
  default     = false
}

variable "extraction_backend" {
  description = "Which LLM extraction backend when enable_llm_extraction is true: \"llm\" (raw-SDK Claude, ADR-0006) or \"pydantic_ai\" (PydanticAI agent, ADR-0014)."
  type        = string
  default     = "llm"
}

variable "llm_model" {
  description = "Claude model id override for feedback extraction. Empty = the selected backend's own default."
  type        = string
  default     = ""
}

variable "enable_redis" {
  type    = bool
  default = false
}

variable "vapi_assistant_id" {
  type    = string
  default = "demo-assistant"
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

variable "enable_demo_routes" {
  description = "Mount the /demo/* seed/reset routes (can wipe tenant data). Demo only; never for a real client."
  type        = bool
  default     = false
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

# RBAC: who is an admin / viewer among the allowlisted operators. With none set,
# every signed-in operator is admin (= pre-RBAC flat behavior). Admins decide
# approvals; viewers are read-only; everyone else is an operator.
variable "auth_admin_emails" {
  type    = string
  default = ""
}

variable "auth_admin_domain" {
  type    = string
  default = ""
}

variable "auth_viewer_emails" {
  type    = string
  default = ""
}

variable "auth_viewer_domain" {
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
