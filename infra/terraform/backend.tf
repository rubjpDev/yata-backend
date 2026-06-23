terraform {
  backend "s3" {
    bucket       = "yata-tfstate-yatarjp"
    key          = "yata-0007/terraform.tfstate"
    region       = "eu-west-1"
    encrypt      = true
    use_lockfile = true
  }
}