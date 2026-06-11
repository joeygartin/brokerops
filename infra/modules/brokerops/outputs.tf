output "api_url" {
  value = google_cloud_run_v2_service.api.uri
}

output "frontend_url" {
  value = google_cloud_run_v2_service.frontend.uri
}

output "sql_connection_name" {
  value = google_sql_database_instance.this.connection_name
}

output "api_service_account" {
  value = google_service_account.api.email
}

output "secret_ids" {
  description = "Secret Manager ids awaiting real values via `make secrets`."
  value       = [for s in google_secret_manager_secret.client : s.secret_id]
}
