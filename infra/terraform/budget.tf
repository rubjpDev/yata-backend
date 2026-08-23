resource "aws_budgets_budget" "monthly" {
  name        = "yata-monthly"
  budget_type = "COST"
  # AWS Budgets only supports USD (yata-0017 incident: EUR was rejected on apply).
  # Limit raised from 10 to 25 USD: yata-0007's 10 was sized for the free-tier
  # t3.micro; yata-0017 pre-approved scaling to t3.small (~17 USD/month) if 1 GB
  # doesn't fit, so a 10-12 USD ceiling would alert during normal operation.
  # Do not lower this back without re-checking the current instance size.
  limit_amount = "25"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = ["rubenjpdev@outlook.com"]
  }
}