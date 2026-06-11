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

variable "cron_schedule" {
  type    = string
  default = "0 13 * * *"
}
