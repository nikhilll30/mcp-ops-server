# Two customer-managed keys, rotated yearly by AWS: one for logs (CloudWatch Logs must be
# named in the key policy), one for data (EFS, Secrets Manager, ECR). The root statement
# lets IAM policies in this account grant use; nothing else is granted here.

locals {
  key_admin_statement = {
    Sid       = "AccountAdministersThisKey"
    Effect    = "Allow"
    Principal = { AWS = "arn:${local.partition}:iam::${local.account_id}:root" }
    Action    = "kms:*"
    Resource  = "*"
  }
}

resource "aws_kms_key" "logs" {
  description             = "${var.name}: CloudWatch Logs (container, VPC flow, WAF)"
  enable_key_rotation     = true
  deletion_window_in_days = 30

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      local.key_admin_statement,
      {
        Sid       = "CloudWatchLogsForThisAccountOnly"
        Effect    = "Allow"
        Principal = { Service = "logs.${local.region}.amazonaws.com" }
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
        Resource  = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:*"
          }
        }
      },
    ]
  })
}

resource "aws_kms_alias" "logs" {
  name          = "alias/${var.name}-logs"
  target_key_id = aws_kms_key.logs.key_id
}

resource "aws_kms_key" "data" {
  description             = "${var.name}: EFS (tokens, audit log, database), the data key secret, ECR"
  enable_key_rotation     = true
  deletion_window_in_days = 30

  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [local.key_admin_statement]
  })
}

resource "aws_kms_alias" "data" {
  name          = "alias/${var.name}-data"
  target_key_id = aws_kms_key.data.key_id
}
