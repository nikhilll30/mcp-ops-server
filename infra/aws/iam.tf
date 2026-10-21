# Four roles, each with only what it uses. No role can read another task's files, call an
# AWS API it does not need, or use the logs key. Policies are jsonencode()d HCL so the
# tests in tests/ can read them.

locals {
  ecs_trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.account_id }
        ArnLike      = { "aws:SourceArn" = "arn:${local.partition}:ecs:${local.region}:${local.account_id}:*" }
      }
    }]
  })
}

# Execution role: what ECS itself needs to start a task (pull the image, write its logs,
# inject the data key). The application never holds these permissions.
resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = local.ecs_trust
}

resource "aws_iam_role_policy" "execution" {
  name = "start-task"
  role = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrLogin"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*" # this action has no resource-level permissions
      },
      {
        Sid      = "PullThisRepositoryOnly"
        Effect   = "Allow"
        Action   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
        Resource = aws_ecr_repository.this.arn
      },
      {
        Sid      = "WriteThisAppsLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.app.arn}:*"
      },
      {
        Sid      = "ReadTheDataKey"
        Effect   = "Allow"
        Action   = "secretsmanager:GetSecretValue"
        Resource = aws_secretsmanager_secret.data_key.arn
      },
      {
        Sid       = "DecryptTheDataKeyViaSecretsManager"
        Effect    = "Allow"
        Action    = "kms:Decrypt"
        Resource  = aws_kms_key.data.arn
        Condition = { StringEquals = { "kms:ViaService" = "secretsmanager.${local.region}.amazonaws.com" } }
      },
    ]
  })
}

locals {
  # EFS permissions per access point: ClientMount alone is read-only.
  service_efs = merge(
    {
      data  = ["elasticfilesystem:ClientMount"]
      audit = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
    },
    var.backend == "sqlite" ? { state = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"] } : {},
  )
  admin_efs = {
    data  = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
    audit = ["elasticfilesystem:ClientMount"]
  }
}

# The server: it can read the tokens file and write its audit log (and database). It cannot
# change the tokens file, so a compromised server cannot grant itself a token.
resource "aws_iam_role" "service_task" {
  name               = "${var.name}-service"
  assume_role_policy = local.ecs_trust
}

resource "aws_iam_role_policy" "service_task" {
  name = "efs"
  role = aws_iam_role.service_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [for ap, actions in local.service_efs : {
      Sid       = "Efs${title(ap)}"
      Effect    = "Allow"
      Action    = actions
      Resource  = aws_efs_file_system.this.arn
      Condition = { StringEquals = { "elasticfilesystem:AccessPointArn" = aws_efs_access_point.this[ap].arn } }
    }]
  })
}

# The admin task: issues and revokes tokens (writes /data), verifies the audit log (reads
# /audit). It cannot write the audit log.
resource "aws_iam_role" "admin_task" {
  name               = "${var.name}-admin"
  assume_role_policy = local.ecs_trust
}

resource "aws_iam_role_policy" "admin_task" {
  name = "efs"
  role = aws_iam_role.admin_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [for ap, actions in local.admin_efs : {
      Sid       = "Efs${title(ap)}"
      Effect    = "Allow"
      Action    = actions
      Resource  = aws_efs_file_system.this.arn
      Condition = { StringEquals = { "elasticfilesystem:AccessPointArn" = aws_efs_access_point.this[ap].arn } }
    }]
  })
}

resource "aws_iam_role" "flow_logs" {
  name = "${var.name}-flow-logs"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "vpc-flow-logs.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })
}

resource "aws_iam_role_policy" "flow_logs" {
  name = "write-flow-logs"
  role = aws_iam_role.flow_logs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
      Resource = "${aws_cloudwatch_log_group.flow.arn}:*"
    }]
  })
}
