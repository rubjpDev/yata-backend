resource "aws_ssm_parameter" "secret_key" {
  name        = "/yata/prod/SECRET_KEY"
  description = "JWT signing key for the API (value set manually, never in git)."
  type        = "SecureString"
  value       = "PLACEHOLDER_SET_MANUALLY"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "database_url" {
  name        = "/yata/prod/DATABASE_URL"
  description = "Connection URL for the on-box Postgres db container."
  type        = "SecureString"
  value       = "PLACEHOLDER_SET_MANUALLY"

  lifecycle {
    ignore_changes = [value]
  }
}
