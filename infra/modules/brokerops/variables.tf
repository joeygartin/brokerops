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

variable "image_version" {
  description = <<-EOT
    Image tag both services are pinned to (ADR-0025). A release is a git tag
    `vMAJOR.MINOR.PATCH`, built once into version-tagged Artifact Registry images;
    a deploy references exactly that tag. `latest` is reserved for the demo/CD
    image-roll and `make deploy-dev` working-tree builds. The full image refs are
    derived from project + region + this tag.
  EOT
  type        = string
  validation {
    condition     = var.image_version != ""
    error_message = "Set image_version — deploy with VERSION=vX.Y.Z (or use `make deploy-dev` for a working-tree build)."
  }
}

variable "enable_langsmith" {
  description = "Inject the LangSmith tracing key + enable tracing env."
  type        = bool
  default     = false
}

variable "enable_llm_extraction" {
  description = "Select an LLM extraction backend and inject its key (ADR-0006/0014); otherwise the deterministic extractor runs."
  type        = bool
  default     = false
}

variable "extraction_backend" {
  description = "Which LLM extraction backend to select when enable_llm_extraction is true (ADR-0014). The app requires this to be explicit — a key alone selects nothing."
  type        = string
  default     = "llm"
  validation {
    condition     = contains(["llm", "pydantic_ai"], var.extraction_backend)
    error_message = "extraction_backend must be \"llm\" or \"pydantic_ai\"."
  }
}

variable "enable_demo_routes" {
  description = "Mount the /demo/* seed/reset routes (can wipe tenant data). Demo only; never for a real client."
  type        = bool
  default     = false
}

variable "llm_model" {
  description = "Claude model id override for feedback extraction. Empty = the selected backend's own default (llm → claude-sonnet-4-6, pydantic_ai → claude-sonnet-5)."
  type        = string
  default     = ""
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

# Outbound business-email provider (EmailPort, ADR-0015). Empty → the bundled
# stub, so a demo/keyless deploy stays zero-credential. "ses" (BOP-016) and
# "sendgrid" (BOP-017) each require their companion config below + their secret
# (make secrets) and fail loud without them — never a silent downgrade.
variable "email_provider" {
  description = "Outbound email provider: \"\" (stub), \"ses\", or \"sendgrid\"."
  type        = string
  default     = ""
  validation {
    condition     = contains(["", "stub", "ses", "sendgrid"], var.email_provider)
    error_message = "email_provider must be one of \"\", \"stub\", \"ses\", \"sendgrid\"."
  }
}

variable "email_base_url" {
  description = "Override base URL for the stub email adapter (tests/tooling); empty = the in-process stub."
  type        = string
  default     = ""
}

# SES companion config (email_provider = \"ses\"). The access-key id and from-
# address are non-secret deploy vars; the secret access key rides in from Secret
# Manager (brokerops-<client>-ses-secret-access-key, pushed by scripts/setup_ses.sh).
variable "ses_region" {
  description = "AWS region for SES; empty = the adapter's default region."
  type        = string
  default     = ""
}

variable "ses_access_key_id" {
  description = "SES IAM access-key id (non-secret); required when email_provider = \"ses\"."
  type        = string
  default     = ""
}

variable "ses_from_address" {
  description = "Verified SES sender address; required when email_provider = \"ses\"."
  type        = string
  default     = ""
}

variable "ses_base_url" {
  description = "Override SES endpoint for an SES-shaped stub (tests/tooling); empty = the real regional endpoint."
  type        = string
  default     = ""
}

# SendGrid companion config (email_provider = \"sendgrid\"). The API key rides in
# from Secret Manager (brokerops-<client>-sendgrid-api-key); the sender and base
# URL are non-secret.
variable "sendgrid_from_email" {
  description = "Domain-authenticated SendGrid sender; required when email_provider = \"sendgrid\"."
  type        = string
  default     = ""
}

variable "sendgrid_base_url" {
  description = "Override SendGrid endpoint for a stub (tests/tooling); empty = the real API."
  type        = string
  default     = ""
}

# Outbound-message drafting backend (BOP-019/020). Empty → deterministic template
# rendering (zero-credential); "pydantic_ai" is the LLM drafter and needs an LLM
# key — the same brokerops-<client>-llm-api-key secret extraction uses, injected
# automatically when this is set (services.tf).
variable "drafting_backend" {
  description = "Outbound drafting backend: \"\" (deterministic) or \"pydantic_ai\" (LLM, BOP-020)."
  type        = string
  default     = ""
  validation {
    condition     = contains(["", "deterministic", "pydantic_ai"], var.drafting_backend)
    error_message = "drafting_backend must be one of \"\", \"deterministic\", \"pydantic_ai\"."
  }
}

# Outbound SMS provider (SMSPort, BOP-018). Empty → the bundled Twilio stub
# (zero-credential); "twilio" requires the auth token (Secret Manager) and its
# sender companions and fails loud without them.
variable "sms_provider" {
  description = "Outbound SMS provider: \"\" (stub) or \"twilio\"."
  type        = string
  default     = ""
  validation {
    condition     = contains(["", "stub", "twilio"], var.sms_provider)
    error_message = "sms_provider must be one of \"\", \"stub\", \"twilio\"."
  }
}

variable "sms_base_url" {
  description = "Override base URL for the SMS provider (stub/tooling); empty = the provider default."
  type        = string
  default     = ""
}

variable "vapi_assistant_id" {
  description = "Vapi assistant id used for outbound feedback calls."
  type        = string
  default     = "demo-assistant"
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

# Operator authentication (ADR-0007). Off → the demo verifier runs and every
# caller is the demo operator, so a key-less deploy stays login-free. On →
# the api verifies Google OIDC ID tokens. The client id is public (it rides
# in the browser sign-in), so it's a plain env var, not a Secret Manager secret.
variable "enable_auth" {
  description = "Require a verified Google operator on the dashboard + API (ADR-0007)."
  type        = bool
  default     = false
}

variable "google_oidc_client_id" {
  description = "Google OAuth web client id the dashboard signs in against; required when enable_auth is true."
  type        = string
  default     = "unset"
}

variable "auth_allowed_domain" {
  description = "Optional Google Workspace domain allowlist (e.g. \"acme.com\"); empty allows any verified Google account."
  type        = string
  default     = ""
}

variable "auth_allowed_emails" {
  description = "Optional comma-separated email allowlist; empty defers to auth_allowed_domain."
  type        = string
  default     = ""
}

variable "auth_methods" {
  description = "Comma-separated login methods to offer: any of \"google\", \"magic\" (ADR-0008). Empty + no client id = demo operator."
  type        = string
  default     = ""
}

# RBAC (ADR-0009): role assignment among allowlisted operators. With none of the
# four set, every signed-in operator is admin (= pre-RBAC flat behavior). Admins
# decide HITL approvals; viewers are read-only; unmatched emails are operators.
variable "auth_admin_emails" {
  description = "Comma-separated emails granted the admin role (can decide approvals)."
  type        = string
  default     = ""
}

variable "auth_admin_domain" {
  description = "Workspace domain whose members get the admin role."
  type        = string
  default     = ""
}

variable "auth_viewer_emails" {
  description = "Comma-separated emails granted the read-only viewer role."
  type        = string
  default     = ""
}

variable "auth_viewer_domain" {
  description = "Workspace domain whose members get the read-only viewer role."
  type        = string
  default     = ""
}

variable "public_base_url" {
  description = "Public URL of the frontend, used to build magic-link emails (e.g. https://app.client.com). Required when \"magic\" is in auth_methods; set explicitly (a custom domain or the frontend run.app URL) — not derived, to avoid an api↔frontend cycle (ADR-0008)."
  type        = string
  default     = ""
}

variable "smtp_host" {
  description = "SMTP server host for magic-link delivery; empty falls back to the console sender (logs the link)."
  type        = string
  default     = ""
}

variable "smtp_port" {
  description = "SMTP server port."
  type        = number
  default     = 587
}

variable "smtp_from" {
  description = "From address on magic-link emails."
  type        = string
  default     = "no-reply@brokerops.app"
}

variable "smtp_username" {
  description = "SMTP auth username; empty sends without login (e.g. an IP-allowlisted relay)."
  type        = string
  default     = ""
}

variable "cron_schedule" {
  description = "Cloud Scheduler cron for the milestone check."
  type        = string
  default     = "0 13 * * *"
}
