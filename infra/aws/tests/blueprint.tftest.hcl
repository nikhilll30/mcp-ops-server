# Plan-time tests of the blueprint's safeguards, with a mocked AWS provider: no account, no
# credentials, no API call. "apply" here applies to the mock, so computed values (ARNs, the
# container definitions JSON) are known. Run with `terraform test` (CI's terraform job).

mock_provider "aws" {
  source = "./tests/mocks"
}

variables {
  image_digest          = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
  certificate_arn       = "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000000"
  allowed_ingress_cidrs = ["10.0.0.0/16"]
  fhir_url              = "https://fhir.internal.example/fhir"
  fhir_cidrs            = ["10.50.0.0/24"]
}

run "task_runs_hardened" {
  command = apply # against the mock provider only: nothing leaves this machine

  assert {
    condition     = jsondecode(aws_ecs_task_definition.service.container_definitions)[0].user == "10001:10001"
    error_message = "The server must run as the image's unprivileged user."
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.service.container_definitions)[0].readonlyRootFilesystem
    error_message = "The root filesystem must be read-only."
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.service.container_definitions)[0].linuxParameters.capabilities.drop == ["ALL"]
    error_message = "Every Linux capability must be dropped."
  }
  assert {
    condition     = !jsondecode(aws_ecs_task_definition.service.container_definitions)[0].privileged
    error_message = "The container must not be privileged."
  }
  assert {
    condition     = endswith(jsondecode(aws_ecs_task_definition.service.container_definitions)[0].image, "@${var.image_digest}")
    error_message = "The image must be pinned by digest."
  }
}

run "data_key_only_from_secrets_manager" {
  command = apply # against the mock provider only: nothing leaves this machine

  assert {
    condition     = [for s in jsondecode(aws_ecs_task_definition.service.container_definitions)[0].secrets : s.name] == ["MCP_OPS_DATA_KEY"]
    error_message = "The data key must be injected from Secrets Manager."
  }
  assert {
    condition     = !contains([for e in jsondecode(aws_ecs_task_definition.service.container_definitions)[0].environment : e.name], "MCP_OPS_DATA_KEY")
    error_message = "The data key must never be a plain environment variable."
  }
  assert {
    condition     = !can(jsondecode(aws_ecs_task_definition.admin.container_definitions)[0].environment) && [for x in jsondecode(aws_ecs_task_definition.admin.container_definitions)[0].secrets : x.name] == ["MCP_OPS_DATA_KEY"]
    error_message = "The admin task must not get the data key as plain environment either."
  }
}

run "environment_for_a_real_deployment" {
  command = apply # against the mock provider only: nothing leaves this machine

  assert {
    condition     = local.environment.MCP_OPS_NOW == "system"
    error_message = "A deployment must use the real clock, not the synthetic demo time."
  }
  assert {
    condition     = local.environment.MCP_OPS_BACKEND == "fhir" && local.environment.MCP_OPS_FHIR_URL == var.fhir_url
    error_message = "The FHIR backend must be configured from fhir_url."
  }
  assert {
    condition     = !contains(keys(local.environment), "MCP_OPS_DB") && !contains(keys(aws_efs_access_point.this), "state")
    error_message = "With FHIR there is no local database and no state volume."
  }
  assert {
    condition     = alltrue([for v in values(local.environment) : v != ""])
    error_message = "No setting may be passed empty (the server refuses set-but-empty numbers)."
  }
  assert {
    condition     = !contains(keys(local.environment), "MCP_OPS_JWT_ISSUER")
    error_message = "JWT settings must be absent unless jwt_issuer is set."
  }
}

run "server_cannot_write_its_own_tokens" {
  command = apply # against the mock provider only: nothing leaves this machine

  assert {
    condition     = one([for m in jsondecode(aws_ecs_task_definition.service.container_definitions)[0].mountPoints : m.readOnly if m.containerPath == "/data"])
    error_message = "The server must mount the tokens volume read-only."
  }
  # The rendered IAM policies, not the locals they are built from (Codex review, PR #22).
  assert {
    condition = alltrue([
      for st in jsondecode(aws_iam_role_policy.service_task.policy).Statement :
      st.Effect == "Allow" && st.Resource == aws_efs_file_system.this.arn
      && contains([for ap in aws_efs_access_point.this : ap.arn], st.Condition.StringEquals["elasticfilesystem:AccessPointArn"])
    ])
    error_message = "Every server EFS grant must be on this file system and bound to one access point."
  }
  assert {
    condition = [
      for st in jsondecode(aws_iam_role_policy.service_task.policy).Statement : st.Action
      if st.Condition.StringEquals["elasticfilesystem:AccessPointArn"] == aws_efs_access_point.this["data"].arn
    ] == [["elasticfilesystem:ClientMount"]]
    error_message = "The server's role must only mount (never write) the tokens access point."
  }
  assert {
    condition = alltrue([
      for st in jsondecode(aws_iam_role_policy.admin_task.policy).Statement :
      st.Resource == aws_efs_file_system.this.arn
      && contains([aws_efs_access_point.this["data"].arn, aws_efs_access_point.this["audit"].arn], st.Condition.StringEquals["elasticfilesystem:AccessPointArn"])
    ])
    error_message = "Every admin EFS grant must be bound to the data or audit access point."
  }
  assert {
    condition = [
      for st in jsondecode(aws_iam_role_policy.admin_task.policy).Statement : st.Action
      if st.Condition.StringEquals["elasticfilesystem:AccessPointArn"] == aws_efs_access_point.this["audit"].arn
    ] == [["elasticfilesystem:ClientMount"]]
    error_message = "The admin task must only mount (never write) the audit access point."
  }
  assert {
    condition     = one([for m in jsondecode(aws_ecs_task_definition.admin.container_definitions)[0].mountPoints : m.readOnly if m.containerPath == "/audit"])
    error_message = "The admin task must mount the audit volume read-only."
  }
}

run "first_apply_starts_no_task" {
  command = apply # against the mock provider only: nothing leaves this machine

  assert {
    condition     = aws_ecs_service.this.desired_count == 0
    error_message = "The first apply must start no task: the image, key and tokens file come after it."
  }
}

run "one_writer_at_a_time" {
  command = apply # against the mock provider only: nothing leaves this machine

  variables {
    start_service = true
  }

  assert {
    condition     = aws_ecs_service.this.desired_count == 1 && aws_ecs_service.this.deployment_maximum_percent == 100 && aws_ecs_service.this.deployment_minimum_healthy_percent == 0
    error_message = "Exactly one task, never two overlapping: the audit log takes an exclusive lock."
  }
  assert {
    condition     = !aws_ecs_service.this.enable_execute_command
    error_message = "ECS Exec must be off."
  }
}

run "private_network_only" {
  command = apply # against the mock provider only: nothing leaves this machine

  assert {
    condition     = aws_lb.this.internal
    error_message = "The load balancer must be internal."
  }
  assert {
    condition     = !one(aws_ecs_service.this.network_configuration).assign_public_ip
    error_message = "Tasks must not get public IPs."
  }
  assert {
    condition     = alltrue([for s in aws_subnet.private : !s.map_public_ip_on_launch])
    error_message = "No subnet may assign public IPs."
  }
  assert {
    condition     = keys(aws_vpc_security_group_egress_rule.service_fhir) == ["10.50.0.0/24"]
    error_message = "The only egress outside the VPC is to fhir_cidrs."
  }
  assert {
    # Evaluated CIDRs, so a computed "0.0.0.0/0" is caught as well as a literal one.
    condition = alltrue([
      for c in concat(
        [for r in aws_vpc_security_group_ingress_rule.alb_https : r.cidr_ipv4],
        [for r in aws_vpc_security_group_egress_rule.service_fhir : r.cidr_ipv4],
      ) : tonumber(split("/", c)[1]) >= 8
    ])
    error_message = "No security group rule may open a range wider than /8."
  }
  assert {
    condition     = one(aws_ecs_cluster_capacity_providers.this.default_capacity_provider_strategy).capacity_provider == "FARGATE"
    error_message = "Fargate must be the cluster default, so run-task never falls back to EC2."
  }
  assert {
    condition     = length(aws_default_security_group.this.ingress) == 0 && length(aws_default_security_group.this.egress) == 0
    error_message = "The VPC's default security group must have no rules."
  }
  assert {
    condition     = jsondecode(aws_vpc_endpoint.s3.policy).Statement[0].Resource == ["arn:aws:s3:::prod-us-east-1-starport-layer-bucket/*"]
    error_message = "The S3 endpoint may only read ECR's image layers."
  }
}

run "tls_and_encryption" {
  command = apply # against the mock provider only: nothing leaves this machine

  assert {
    condition     = aws_lb_listener.https.protocol == "HTTPS" && aws_lb_listener.https.ssl_policy == "ELBSecurityPolicy-TLS13-1-2-Res-2021-06"
    error_message = "The listener must be HTTPS with TLS 1.2+ only."
  }
  assert {
    condition     = aws_kms_key.logs.enable_key_rotation && aws_kms_key.data.enable_key_rotation
    error_message = "Both KMS keys must rotate."
  }
  assert {
    condition     = aws_efs_file_system.this.encrypted
    error_message = "EFS must be encrypted at rest."
  }
  assert {
    condition     = alltrue([for v in aws_ecs_task_definition.service.volume : one(v.efs_volume_configuration).transit_encryption == "ENABLED" && one(one(v.efs_volume_configuration).authorization_config).iam == "ENABLED"])
    error_message = "Every EFS volume must use TLS and IAM authorization."
  }
  assert {
    condition     = alltrue([for g in [aws_cloudwatch_log_group.app, aws_cloudwatch_log_group.flow, aws_cloudwatch_log_group.waf] : g.retention_in_days >= 400])
    error_message = "Logs must be kept at least 400 days."
  }
  assert {
    condition     = alltrue([for st in jsondecode(aws_efs_file_system_policy.this.policy).Statement : st.Effect == "Deny" && st.Principal == { AWS = "*" } && st.Resource == aws_efs_file_system.this.arn])
    error_message = "The EFS file system policy may only deny, for every principal, on this file system."
  }
  assert {
    condition = anytrue([
      for st in jsondecode(aws_efs_file_system_policy.this.policy).Statement :
      st.Action == "*" && try(st.Condition.Bool["aws:SecureTransport"], "") == "false"
    ])
    error_message = "The EFS policy must deny every action without TLS."
  }
  assert {
    condition     = anytrue([for st in jsondecode(aws_efs_file_system_policy.this.policy).Statement : st.Action == "elasticfilesystem:ClientRootAccess" && !can(st.Condition)])
    error_message = "The EFS policy must deny root access unconditionally."
  }
  assert {
    condition = anytrue([
      for st in jsondecode(aws_efs_file_system_policy.this.policy).Statement :
      contains(try(tolist(st.Action), []), "elasticfilesystem:ClientMount") && try(st.Condition.Null["elasticfilesystem:AccessPointArn"], "") == "true"
    ])
    error_message = "The EFS policy must deny mounts that bypass an access point."
  }
  assert {
    condition = anytrue([
      for st in jsondecode(aws_efs_file_system_policy.this.policy).Statement :
      contains(try(tolist(st.Action), []), "elasticfilesystem:ClientWrite")
      && toset(try(st.Condition.ArnNotEquals["aws:PrincipalArn"], [])) == toset([aws_iam_role.service_task.arn, aws_iam_role.admin_task.arn])
    ])
    error_message = "The EFS policy must deny every principal but the two task roles."
  }
  assert {
    condition     = one(one(aws_wafv2_web_acl_logging_configuration.this.redacted_fields).single_header).name == "authorization"
    error_message = "WAF logs must redact the bearer token."
  }
  assert {
    condition = alltrue(concat(
      [for v in aws_wafv2_web_acl.this.visibility_config : !v.sampled_requests_enabled],
      [for r in aws_wafv2_web_acl.this.rule : alltrue([for v in r.visibility_config : !v.sampled_requests_enabled])],
    ))
    error_message = "WAF request sampling must be off: samples keep the Authorization header, unredacted."
  }
  assert {
    condition = (
      jsondecode(aws_cloudwatch_log_resource_policy.waf.policy_document).Statement[0].Principal.Service == "delivery.logs.amazonaws.com"
      && jsondecode(aws_cloudwatch_log_resource_policy.waf.policy_document).Statement[0].Resource == "${aws_cloudwatch_log_group.waf.arn}:*"
    )
    error_message = "WAF log delivery must be allowed on the WAF log group only."
  }
}

run "log_writers_may_use_the_logs_key_only_for_their_group" {
  command = apply # against the mock provider only: nothing leaves this machine

  assert {
    condition = alltrue([
      for st in concat(
        jsondecode(aws_iam_role_policy.execution.policy).Statement,
        jsondecode(aws_iam_role_policy.flow_logs.policy).Statement,
      ) : st.Resource == aws_kms_key.logs.arn && st.Condition.StringEquals["kms:ViaService"] == "logs.us-east-1.amazonaws.com"
      if contains(try(tolist(st.Action), [st.Action]), "kms:GenerateDataKey")
    ])
    error_message = "Logs-key grants must be on the logs key, through CloudWatch Logs only."
  }
  assert {
    condition = [
      for st in jsondecode(aws_iam_role_policy.flow_logs.policy).Statement :
      st.Condition.StringEquals["kms:EncryptionContext:aws:logs:arn"]
      if contains(try(tolist(st.Action), [st.Action]), "kms:GenerateDataKey")
    ] == [aws_cloudwatch_log_group.flow.arn]
    error_message = "The flow-log role may use the logs key for the flow log group only."
  }
  assert {
    condition     = output.token_audience == "mcp-ops-server" && output.jwks_path == "/data/jwks.json"
    error_message = "The outputs must give the audience and JWKS path the admin commands need."
  }
}

run "sqlite_demo_mode" {
  command = apply # against the mock provider only: nothing leaves this machine

  variables {
    backend    = "sqlite"
    fhir_url   = ""
    fhir_cidrs = []
  }

  assert {
    condition     = local.environment.MCP_OPS_DB == "/state/clinic.db" && !contains(keys(local.environment), "MCP_OPS_FHIR_URL")
    error_message = "SQLite mode keeps its encrypted file on the state volume."
  }
  assert {
    condition     = contains(keys(aws_efs_access_point.this), "state") && length(aws_vpc_security_group_egress_rule.service_fhir) == 0
    error_message = "SQLite mode has a state volume and no FHIR egress."
  }
}

run "jwt_from_a_file_in_the_data_volume" {
  command = apply # against the mock provider only: nothing leaves this machine

  variables {
    jwt_issuer = "https://idp.internal.example"
  }

  assert {
    condition     = local.environment.MCP_OPS_JWT_JWKS == "/data/jwks.json" && local.environment.MCP_OPS_JWT_REVOKED_FILE == "/data/revoked.json"
    error_message = "JWT keys and revocations must come from files in the data volume."
  }
}
