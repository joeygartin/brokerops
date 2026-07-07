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

# Owner/migration role: owns the tables (alembic runs as this role) and manages
# schema. Cloud SQL makes it a cloudsqlsuperuser member; it is NOT a real Postgres
# superuser, so it cannot bypass RLS — but it IS the table owner, so migration 0007
# uses FORCE RLS to bind the policy to it too.
resource "google_sql_user" "app" {
  project  = var.project_id
  name     = "brokerops"
  instance = google_sql_database_instance.this.name
  password = random_password.db.result
}

resource "random_password" "db_app" {
  length  = 24
  special = false
}

# Runtime least-privilege role (BOP-013 / ADR-0021): the app's tenant-scoped
# domain stores connect as this role. It is NOT the table owner and has NO
# BYPASSRLS attribute, so the forced RLS policy binds to every query, and
# migration 0010 grants it DML only — it cannot run DDL or disable RLS.
resource "google_sql_user" "runtime" {
  project  = var.project_id
  name     = "brokerops_app"
  instance = google_sql_database_instance.this.name
  password = random_password.db_app.result
}

locals {
  # psycopg + SQLAlchemy both accept the Cloud SQL unix-socket host form.
  # Owner DSN — alembic migrations and the LangGraph checkpointer's setup().
  database_url = "postgresql://brokerops:${random_password.db.result}@/brokerops_${var.client_name}?host=/cloudsql/${google_sql_database_instance.this.connection_name}"
  # Runtime least-privilege DSN — the tenant-scoped domain stores (BOP-013).
  app_database_url = "postgresql://brokerops_app:${random_password.db_app.result}@/brokerops_${var.client_name}?host=/cloudsql/${google_sql_database_instance.this.connection_name}"
}
