#!/usr/bin/env bash
# infra/deploy_box.sh — what the box runs on every deploy (R10).
#
# Delivered by the GitHub Actions "Deploy via SSM" step (R7) alongside
# docker-compose.prod.yml and infra/Caddyfile, then executed under
# `/opt/yata`. Every step fails loudly rather than continuing (R10).
#
# Sole writer of /opt/yata/.env (R14): rebuilt from scratch on every run as
# /opt/yata/host.env (written once, at first boot, by Terraform's user_data —
# CADDY_HOST and ECR_IMAGE only) plus the secrets read here from SSM. The ECR
# login also runs here, on every deploy, not in user_data (R11): an ECR
# authorization token lives 12 hours, so a one-shot login at first boot dies
# on the box's second deploy.
set -euo pipefail

cd /opt/yata

: "${AWS_REGION:=eu-west-1}"

if [ ! -f host.env ]; then
  echo "FATAL: /opt/yata/host.env is missing — user_data has not run yet" >&2
  exit 1
fi
# shellcheck disable=SC1091
. ./host.env

REGISTRY="${ECR_IMAGE%%/*}"
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"

SECRET_KEY="$(aws ssm get-parameter --name /yata/prod/SECRET_KEY --with-decryption \
  --query Parameter.Value --output text --region "$AWS_REGION")"
POSTGRES_PASSWORD="$(aws ssm get-parameter --name /yata/prod/POSTGRES_PASSWORD --with-decryption \
  --query Parameter.Value --output text --region "$AWS_REGION")"

install -m 600 /dev/null .env
{
  cat host.env
  echo "SECRET_KEY=${SECRET_KEY}"
  echo "POSTGRES_PASSWORD=${POSTGRES_PASSWORD}"
} > .env

docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d

# ponytail: one line, no retention policy, no cron, no lifecycle rule (R12).
# The API image carries onnxruntime and BGE; three undeleted :latest layers
# fill the AMI-default root volume and the next deploy fails on disk, not on
# code. Ceiling: an ECR lifecycle policy the day image history matters.
docker image prune -f
