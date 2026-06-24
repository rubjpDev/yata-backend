resource "aws_ecr_repository" "api" {
  name                 = "yata-api"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Name = "yata-api" }
}