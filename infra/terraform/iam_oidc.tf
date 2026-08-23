resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "deploy_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name               = "yata-deploy"
  assume_role_policy = data.aws_iam_policy_document.deploy_trust.json
}

data "aws_iam_policy_document" "deploy_perms" {
  statement {
    sid       = "EcrAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "EcrPushPull"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.api.arn]
  }

  statement {
    sid     = "SsmDeploy"
    effect  = "Allow"
    actions = ["ssm:SendCommand"]
    resources = [
      aws_instance.api.arn,
      "arn:aws:ssm:${var.region}::document/AWS-RunShellScript",
    ]
  }

  # R21/R22/R70: without this the workflow can `send-command` but can never
  # learn whether the box succeeded, so a deploy that crashes on the box
  # still reports a green Actions run.
  statement {
    sid    = "SsmWaitForDeploy"
    effect = "Allow"
    actions = [
      "ssm:GetCommandInvocation",
      "ssm:ListCommandInvocations",
    ]
    resources = ["*"]
  }

  # deploy.yml:110 resolves the box with `aws ec2 describe-instances` before
  # sending the SSM command. `ec2:DescribeInstances` does not support
  # resource-level permissions, so scoping `resources` to the instance ARN
  # here would look tighter but silently deny every call.
  statement {
    sid       = "Ec2DescribeInstances"
    effect    = "Allow"
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "yata-deploy-perms"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy_perms.json
}

data "aws_iam_policy_document" "ec2_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ec2" {
  name               = "yata-ec2"
  assume_role_policy = data.aws_iam_policy_document.ec2_trust.json
}

resource "aws_iam_role_policy_attachment" "ec2_ssm" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "ec2_ecr" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_instance_profile" "ec2" {
  name = "yata-ec2"
  role = aws_iam_role.ec2.name
}

# The AWS-managed key SSM SecureStrings decrypt through by default
# (`alias/aws/ssm`), looked up rather than hardcoded so the ARN always
# matches the account it runs in.
data "aws_kms_alias" "ssm" {
  name = "alias/aws/ssm"
}

# yata-0019: the account id is resolved live rather than hardcoded, since
# this repo is public and the id is not something we want to publish.
data "aws_caller_identity" "current" {}

# R15/R68: `AmazonSSMManagedInstanceCore` (attached above) scopes
# `ssm:GetParameter` to `arn:aws:ssm:*:*:parameter/aws/ssm/*` and grants no
# `kms:Decrypt` at all, so `deploy_box.sh`'s
# `--with-decryption` read of `/yata/prod/*` fails with AccessDenied without
# this inline policy.
data "aws_iam_policy_document" "ec2_ssm_parameters" {
  statement {
    sid       = "ReadYataProdParameters"
    effect    = "Allow"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = ["arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/yata/prod/*"]
  }

  statement {
    sid       = "DecryptYataProdParameters"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [data.aws_kms_alias.ssm.target_key_arn]
  }
}

resource "aws_iam_role_policy" "ec2_ssm_parameters" {
  name   = "yata-ec2-ssm-parameters"
  role   = aws_iam_role.ec2.id
  policy = data.aws_iam_policy_document.ec2_ssm_parameters.json
}

# R16/R69: the instance role has no Bedrock statement of any kind today, and
# `app/llm.py::BedrockConverseClient._get_client` builds its `boto3` client
# with the instance's ambient credentials — so every production coach run
# would fail with AccessDenied without this, on the one feature the demo
# exists to show.
data "aws_iam_policy_document" "ec2_bedrock" {
  statement {
    sid    = "InvokeCoachModel"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    # Tracks app/config.py::Settings.llm_model (default
    # "qwen.qwen3-next-80b-a3b"); update both together if the model changes.
    resources = ["arn:aws:bedrock:${var.region}::foundation-model/${var.llm_model}"]
  }
}

resource "aws_iam_role_policy" "ec2_bedrock" {
  name   = "yata-ec2-bedrock"
  role   = aws_iam_role.ec2.id
  policy = data.aws_iam_policy_document.ec2_bedrock.json
}