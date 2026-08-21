resource "aws_ssm_parameter" "secret_key" {
  name        = "/yata/prod/SECRET_KEY"
  description = "JWT signing key for the API (value set manually, never in git)."
  type        = "SecureString"
  value       = "PLACEHOLDER_SET_MANUALLY"

  lifecycle {
    ignore_changes = [value]
  }
}

# R17: only the password is a secret. The host, user, database name and
# driver are topology the compose file owns and must agree with, so they stay
# in docker-compose.prod.yml, not here. This parameter is interpolated into
# both the api's DATABASE_URL and the db service's own POSTGRES_PASSWORD, so
# one variable used twice can never drift the way two independent copies of
# the same password would.
resource "aws_ssm_parameter" "postgres_password" {
  name        = "/yata/prod/POSTGRES_PASSWORD"
  description = "Postgres password for the on-box db container (value set manually, never in git)."
  type        = "SecureString"
  value       = "PLACEHOLDER_SET_MANUALLY"

  lifecycle {
    ignore_changes = [value]
  }
}
