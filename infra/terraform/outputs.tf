output "api_eip" {
  description = "Public Elastic IP of the API box."
  value       = aws_eip.api.public_ip
}

output "api_sslip_host" {
  description = "Hostname Caddy uses for TLS (<eip>.sslip.io)."
  value       = "${aws_eip.api.public_ip}.sslip.io"
}

output "ecr_repo_url" {
  description = "ECR repository URL for the API image."
  value       = aws_ecr_repository.api.repository_url
}