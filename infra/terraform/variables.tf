variable "region" {
  description = "AWS region for all resources."
  type        = string
  default     = "eu-west-1"
}

variable "instance_type" {
  description = "EC2 instance type for the API box."
  type        = string
  default     = "t3.micro"
}

variable "github_repo" {
  description = "GitHub repo allowed to assume the deploy role, owner/name."
  type        = string
  default     = "rubjpDev/yata-backend"
}