# One small Cloud SQL instance per client. Cheap isolation, self-contained
# teardown. If client density ever justifies it, consolidate to a shared
# instance per environment with database-per-client — the DSN secret is the
# only thing that changes.

resource "google_sql_database_instance" "this" {
  project          = var.project_id
  name             = local.prefix
  database_version = "POSTGRES_16"
  region           = var.region

  settings {
    # Standard edition: required for shared-core pilot tiers like db-f1-micro
    # (the Enterprise Plus default only allows perf-optimized machine types).
    edition = "ENTERPRISE"
    tier    = var.db_tier
    ip_configuration {
      ipv4_enabled = true # connections only via the Cloud SQL connector socket
    }
  }

  deletion_protection = false
}

resource "google_sql_database" "db" {
  project  = var.project_id
  name     = "brokerops_${var.client_name}"
  instance = google_sql_database_instance.this.name
}

resource "random_password" "db" {
  length  = 24
  special = false
}

resource "google_sql_user" "app" {
  project  = var.project_id
  name     = "brokerops"
  instance = google_sql_database_instance.this.name
  password = random_password.db.result
}

locals {
  # psycopg + SQLAlchemy both accept the Cloud SQL unix-socket host form.
  database_url = "postgresql://brokerops:${random_password.db.result}@/brokerops_${var.client_name}?host=/cloudsql/${google_sql_database_instance.this.connection_name}"
}
