resource "aws_budgets_budget" "monthly" {
  name         = "yata-monthly"
  budget_type  = "COST"
  limit_amount = "10"
  limit_unit   = "EUR"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = ["rubenjpdev@outlook.com"]
  }
}