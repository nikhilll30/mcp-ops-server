# EFS holds the three things the server keeps on disk, each behind its own access point that
# forces uid/gid 10001 and a fixed root directory:
#   /data   the tokens file (and JWT files): read-only to the server, written by the admin task
#   /audit  the encrypted, hash-chained audit log: written by the server only
#   /state  the encrypted SQLite file (backend = "sqlite" only)
# The server takes an exclusive lock on the audit log and the database, so the service runs
# exactly one task (main.tf).

locals {
  access_points = merge(
    { data = { path = "/data", perms = "0755" }, audit = { path = "/audit", perms = "0700" } },
    var.backend == "sqlite" ? { state = { path = "/state", perms = "0700" } } : {},
  )
}

resource "aws_efs_file_system" "this" {
  encrypted        = true
  kms_key_id       = aws_kms_key.data.arn
  performance_mode = "generalPurpose"
  throughput_mode  = "elastic"

  tags = { Name = var.name }
}

# Daily backups by AWS Backup (35-day retention, the service default plan).
resource "aws_efs_backup_policy" "this" {
  file_system_id = aws_efs_file_system.this.id

  backup_policy {
    status = "ENABLED"
  }
}

resource "aws_efs_mount_target" "this" {
  count = local.az_count

  file_system_id  = aws_efs_file_system.this.id
  subnet_id       = aws_subnet.private[count.index].id
  security_groups = [aws_security_group.efs.id]
}

resource "aws_efs_access_point" "this" {
  for_each = local.access_points

  file_system_id = aws_efs_file_system.this.id

  posix_user {
    uid = local.app_uid
    gid = local.app_uid
  }

  root_directory {
    path = each.value.path
    creation_info {
      owner_uid   = local.app_uid
      owner_gid   = local.app_uid
      permissions = each.value.perms
    }
  }

  tags = { Name = "${var.name}-${each.key}" }
}

# Resource policy: TLS only, through an access point only, never root, and only the two task
# roles may mount at all. Which access point each role may write is in iam.tf.
resource "aws_efs_file_system_policy" "this" {
  file_system_id = aws_efs_file_system.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyWithoutTls"
        Effect    = "Deny"
        Principal = { AWS = "*" }
        Action    = "*"
        Resource  = aws_efs_file_system.this.arn
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        Sid       = "DenyRootAccess"
        Effect    = "Deny"
        Principal = { AWS = "*" }
        Action    = "elasticfilesystem:ClientRootAccess"
        Resource  = aws_efs_file_system.this.arn
      },
      {
        Sid       = "DenyMountWithoutAccessPoint"
        Effect    = "Deny"
        Principal = { AWS = "*" }
        Action    = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
        Resource  = aws_efs_file_system.this.arn
        Condition = { Null = { "elasticfilesystem:AccessPointArn" = "true" } }
      },
      {
        Sid       = "DenyOtherPrincipals"
        Effect    = "Deny"
        Principal = { AWS = "*" }
        Action    = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
        Resource  = aws_efs_file_system.this.arn
        Condition = {
          ArnNotEquals = { "aws:PrincipalArn" = [aws_iam_role.service_task.arn, aws_iam_role.admin_task.arn] }
        }
      },
    ]
  })
}

# The data key lives here. Terraform creates the empty secret; its value is set once, out of
# band (README.md), so the key never enters Terraform state or a plan.
resource "aws_secretsmanager_secret" "data_key" {
  #checkov:skip=CKV2_AWS_57:The server cannot rotate its data key yet (README Known limits: a new key means a new audit log and database), so automatic rotation would lock it out of its own audit log.
  name                    = "${var.name}/data-key"
  description             = "MCP_OPS_DATA_KEY: encrypts the audit log and the SQLite file. Set with python -m mcp_ops_kit.crypto new-key."
  kms_key_id              = aws_kms_key.data.arn
  recovery_window_in_days = 30
}

# The image, copied here from the CI-tested GHCR image (README.md). Tags are immutable and
# the task definition pins a digest anyway. No lifecycle policy: expiring old images could
# delete the digest a running task definition still names.
resource "aws_ecr_repository" "this" {
  name                 = var.name
  image_tag_mutability = "IMMUTABLE"

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.data.arn
  }

  image_scanning_configuration {
    scan_on_push = true
  }
}
