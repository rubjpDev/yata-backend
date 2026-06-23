data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }
}

resource "aws_instance" "api" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.api.id]
  iam_instance_profile   = aws_iam_instance_profile.ec2.name

  user_data                   = local.user_data
  user_data_replace_on_change = false

  tags = { Name = "yata-api" }
}

resource "aws_eip" "api" {
  domain = "vpc"
  tags   = { Name = "yata-api-eip" }
}

resource "aws_eip_association" "api" {
  instance_id   = aws_instance.api.id
  allocation_id = aws_eip.api.id
}

resource "aws_ebs_volume" "data" {
  availability_zone = aws_instance.api.availability_zone
  size              = 10
  type              = "gp3"
  tags              = { Name = "yata-db-data" }
}

resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.api.id
}

locals {
  caddy_host = "${aws_eip.api.public_ip}.sslip.io"

  user_data = <<-EOF
    #!/bin/bash
    set -euo pipefail

    # 1) Docker + plugin compose (Amazon Linux 2023)
    dnf install -y docker
    systemctl enable --now docker
    DOCKER_CONFIG=/usr/local/lib/docker
    mkdir -p $DOCKER_CONFIG/cli-plugins
    curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
      -o $DOCKER_CONFIG/cli-plugins/docker-compose
    chmod +x $DOCKER_CONFIG/cli-plugins/docker-compose

    # 2) Montar el volumen EBS de datos en /data (formatea solo si está vacío)
    DEVICE=/dev/nvme1n1
    if ! blkid $DEVICE; then mkfs -t ext4 $DEVICE; fi
    mkdir -p /data
    mount $DEVICE /data
    echo "$DEVICE /data ext4 defaults,nofail 0 2" >> /etc/fstab

    # 3) El host de Caddy = <eip>.sslip.io (la EIP solo se conoce ahora)
    mkdir -p /opt/yata
    echo "CADDY_HOST=${local.caddy_host}" >  /opt/yata/.env
    echo "ECR_IMAGE=${aws_ecr_repository.api.repository_url}:latest" >> /opt/yata/.env

    # 4) Login en ECR y traer compose + arrancar
    aws ecr get-login-password --region ${var.region} \
      | docker login --username AWS --password-stdin ${aws_ecr_repository.api.repository_url}
    # docker-compose.prod.yml y Caddyfile se entregan por el deploy de CI (ssm send-command)
    EOF
}