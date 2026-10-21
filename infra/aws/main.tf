# One Fargate service running the published image, pinned by digest, as the image's own
# unprivileged user on a read-only root filesystem with every Linux capability dropped.

locals {
  image = "${aws_ecr_repository.this.repository_url}@${var.image_digest}"

  # Every MCP_OPS_* setting the task gets (.env.example documents each one). Empty values
  # are left out: the server treats a set-but-empty number as a mistake, by design.
  environment = { for k, v in merge(
    {
      MCP_OPS_HOST               = "0.0.0.0" # check_infra: bind address (listen inside the task)
      MCP_OPS_PORT               = tostring(local.port)
      MCP_OPS_BACKEND            = var.backend
      MCP_OPS_TOKENS_FILE        = "/data/tokens.json"
      MCP_OPS_AUDIT_LOG          = "/audit/audit.log"
      MCP_OPS_NOW                = "system" # the real clock, never the synthetic demo time
      MCP_OPS_RATE_LIMIT_PER_MIN = tostring(var.rate_limit_per_minute)
      MCP_OPS_AUDIENCE           = var.audience
    },
    var.backend == "fhir" ? { MCP_OPS_FHIR_URL = var.fhir_url } : { MCP_OPS_DB = "/state/clinic.db" },
    var.jwt_issuer == "" ? {} : {
      MCP_OPS_JWT_ISSUER       = var.jwt_issuer
      MCP_OPS_JWT_JWKS         = "/data/${var.jwt_jwks_file}"
      MCP_OPS_JWT_REVOKED_FILE = "/data/revoked.json"
    },
  ) : k => v if v != "" }

  # The data key comes from Secrets Manager at start-up, never from plain environment.
  secrets = [{ name = "MCP_OPS_DATA_KEY", valueFrom = aws_secretsmanager_secret.data_key.arn }]

  hardening = {
    user                   = "${local.app_uid}:${local.app_uid}"
    readonlyRootFilesystem = true
    privileged             = false
    linuxParameters        = { capabilities = { drop = ["ALL"] }, initProcessEnabled = true }
  }

  service_mounts = concat(
    [
      { sourceVolume = "data", containerPath = "/data", readOnly = true },
      { sourceVolume = "audit", containerPath = "/audit", readOnly = false },
    ],
    var.backend == "sqlite" ? [{ sourceVolume = "state", containerPath = "/state", readOnly = false }] : [],
  )
}

resource "aws_ecs_cluster" "this" {
  name = var.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

# Fargate is the cluster's default, so a run-task without --launch-type still lands on
# Fargate rather than on EC2 capacity this cluster does not have (Codex review, PR #22).
resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/${var.name}/app"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.logs.arn
}

resource "aws_ecs_task_definition" "service" {
  family                   = "${var.name}-service"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.service_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  dynamic "volume" {
    for_each = { for k, v in aws_efs_access_point.this : k => v.id }
    content {
      name = volume.key
      efs_volume_configuration {
        file_system_id     = aws_efs_file_system.this.id
        transit_encryption = "ENABLED"
        authorization_config {
          access_point_id = volume.value
          iam             = "ENABLED"
        }
      }
    }
  }

  container_definitions = jsonencode([merge(local.hardening, {
    name         = "mcp-ops-server"
    image        = local.image
    essential    = true
    portMappings = [{ containerPort = local.port, protocol = "tcp" }]
    environment  = [for k in sort(keys(local.environment)) : { name = k, value = local.environment[k] }]
    secrets      = local.secrets
    mountPoints  = local.service_mounts
    stopTimeout  = 30
    healthCheck = {
      command     = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${local.port}/healthz', timeout=2)"]
      interval    = 10
      timeout     = 3
      retries     = 3
      startPeriod = 10
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app.name
        awslogs-region        = local.region
        awslogs-stream-prefix = "service"
      }
    }
  })])
}

resource "aws_ecs_service" "this" {
  name             = var.name
  cluster          = aws_ecs_cluster.this.id
  task_definition  = aws_ecs_task_definition.service.arn
  launch_type      = "FARGATE"
  platform_version = "LATEST" # 1.4.0 or later: EFS volumes need it
  propagate_tags   = "SERVICE"

  # At most one task, and the old one stops before the new one starts: the audit log and the
  # database each take an exclusive lock (README Known limits), so two tasks cannot overlap.
  # Zero until start_service: a first deployment with no image, key or tokens file yet would
  # fail, and the circuit breaker would then stop launching (Cursor Bugbot, PR #22).
  desired_count                      = var.start_service ? 1 : 0
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  enable_execute_command             = false # no shell into a task that holds PHI
  health_check_grace_period_seconds  = 30

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.service.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.this.arn
    container_name   = "mcp-ops-server"
    container_port   = local.port
  }

  depends_on = [aws_lb_listener.https, aws_efs_mount_target.this]
}

# The admin task: run on demand with a command override (README.md), e.g.
#   python -m mcp_ops_server.auth issue --file /data/tokens.json --client ... --scope ... \
#     --sha256 <hash of a token made on your machine>
# Never plain "issue" here: it prints the token, and this task's output goes to CloudWatch.
#   python -m mcp_ops_server.auth revoke --file /data/tokens.json --client ...
#   python -m mcp_ops_kit.audit verify --file /audit/audit.log --key-env MCP_OPS_DATA_KEY
# It mounts /data read-write and /audit read-only, has no inbound network and no service.
resource "aws_ecs_task_definition" "admin" {
  family                   = "${var.name}-admin"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.admin_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  dynamic "volume" {
    for_each = { for k in ["data", "audit"] : k => aws_efs_access_point.this[k].id }
    content {
      name = volume.key
      efs_volume_configuration {
        file_system_id     = aws_efs_file_system.this.id
        transit_encryption = "ENABLED"
        authorization_config {
          access_point_id = volume.value
          iam             = "ENABLED"
        }
      }
    }
  }

  container_definitions = jsonencode([merge(local.hardening, {
    name      = "admin"
    image     = local.image
    essential = true
    command   = ["python", "-m", "mcp_ops_server.auth", "--help"]
    secrets   = local.secrets
    mountPoints = [
      { sourceVolume = "data", containerPath = "/data", readOnly = false },
      { sourceVolume = "audit", containerPath = "/audit", readOnly = true },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app.name
        awslogs-region        = local.region
        awslogs-stream-prefix = "admin"
      }
    }
  })])
}
