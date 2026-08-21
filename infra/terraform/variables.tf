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

variable "llm_model" {
  description = "Bedrock model id the EC2 role is scoped to invoke. Tracks app/config.py::Settings.llm_model."
  type        = string
  default     = "qwen.qwen3-next-80b-a3b"
}