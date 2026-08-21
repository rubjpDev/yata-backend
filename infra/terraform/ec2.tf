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

    # 2) Resolve the data EBS volume by asking the kernel, never by assuming a
    #    device name (R23): Terraform attaches it as /dev/sdf but Nitro
    #    instances rename NVMe devices, so /dev/nvme1n1 was only ever a guess.
    #    The root device is excluded; whatever unmounted, unpartitioned block
    #    device remains is the data volume. Zero or more than one candidate is
    #    a hard failure, logged in full to /var/log/cloud-init-output.log.
    ROOT_DEVICE="$(lsblk -dpno PKNAME "$(findmnt -no SOURCE /)" 2>/dev/null || true)"
    mapfile -t CANDIDATES < <(lsblk -dpno NAME,MOUNTPOINT,TYPE | awk '$3=="disk" && $2=="" {print $1}')
    FILTERED=()
    for candidate in "$${CANDIDATES[@]}"; do
      if [ -n "$ROOT_DEVICE" ] && [ "$candidate" = "$ROOT_DEVICE" ]; then
        continue
      fi
      FILTERED+=("$candidate")
    done
    if [ "$${#FILTERED[@]}" -ne 1 ]; then
      echo "FATAL: expected exactly one unmounted, unpartitioned, non-root block device; found $${#FILTERED[@]}:" >&2
      lsblk -dpno NAME,MOUNTPOINT,TYPE,PKNAME >&2
      exit 1
    fi
    DEVICE="$${FILTERED[0]}"

    # 3) Mount it without reformatting if it already carries a filesystem
    #    (R24): only format ext4 when empty. fstab keeps `nofail` so a boot
    #    never hangs on a missing volume, and is written BY UUID because the
    #    resolved device name is not guaranteed stable across reboots.
    if ! blkid "$DEVICE" >/dev/null 2>&1; then
      mkfs -t ext4 "$DEVICE"
    fi
    mkdir -p /data
    mount "$DEVICE" /data
    UUID="$(blkid -s UUID -o value "$DEVICE")"
    if ! grep -q "$UUID" /etc/fstab; then
      echo "UUID=$UUID /data ext4 defaults,nofail 0 2" >> /etc/fstab
    fi

    # 4) 2 GB swap file on /data (R26): a cushion against the box's 1 GB of
    #    RAM, not an answer — it turns an OOM-kill mid-request into a slow
    #    request. Idempotent: skipped if already active.
    if ! swapon --show | grep -q /data/swapfile; then
      if [ ! -f /data/swapfile ]; then
        fallocate -l 2G /data/swapfile
        chmod 600 /data/swapfile
        mkswap /data/swapfile
      fi
      swapon /data/swapfile
    fi
    if ! grep -q /data/swapfile /etc/fstab; then
      echo "/data/swapfile none swap sw 0 0" >> /etc/fstab
    fi

    # 5) Write only the facts Terraform knows to host.env (R13): CADDY_HOST
    #    and ECR_IMAGE. user_data is the SOLE writer of this file — secrets
    #    and the rebuilt .env are deploy_box.sh's job, on every deploy, never
    #    here (R7, R11: the ECR login lives in deploy_box.sh so it is renewed
    #    every deploy, not once at first boot).
    mkdir -p /opt/yata
    echo "CADDY_HOST=${local.caddy_host}" > /opt/yata/host.env
    echo "ECR_IMAGE=${aws_ecr_repository.api.repository_url}:latest" >> /opt/yata/host.env
    EOF
}
