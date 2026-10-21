# Every path is one rule, named, from one group to another. No group allows 0.0.0.0/0.

resource "aws_security_group" "alb" {
  name        = "${var.name}-alb"
  description = "Internal load balancer: HTTPS in from allowed_ingress_cidrs, out to the tasks only"
  vpc_id      = aws_vpc.this.id
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  for_each = toset(var.allowed_ingress_cidrs)

  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from an allowed network"
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "alb_to_tasks" {
  security_group_id            = aws_security_group.alb.id
  description                  = "To the MCP server tasks"
  referenced_security_group_id = aws_security_group.service.id
  ip_protocol                  = "tcp"
  from_port                    = local.port
  to_port                      = local.port
}

resource "aws_security_group" "service" {
  name        = "${var.name}-service"
  description = "MCP server tasks: in from the load balancer; out to endpoints, EFS and FHIR"
  vpc_id      = aws_vpc.this.id
}

resource "aws_vpc_security_group_ingress_rule" "service_from_alb" {
  security_group_id            = aws_security_group.service.id
  description                  = "From the load balancer"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = local.port
  to_port                      = local.port
}

resource "aws_vpc_security_group_egress_rule" "service_fhir" {
  for_each = toset(var.backend == "fhir" ? var.fhir_cidrs : [])

  security_group_id = aws_security_group.service.id
  description       = "HTTPS to the FHIR server"
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

# The one-off admin task (issue/revoke tokens, verify the audit log): no ingress at all.
resource "aws_security_group" "admin" {
  #checkov:skip=CKV2_AWS_5:Attached at run time by aws ecs run-task (outputs.admin_network), not by a Terraform resource.
  name        = "${var.name}-admin"
  description = "Admin task: no inbound; out to endpoints and EFS only"
  vpc_id      = aws_vpc.this.id
}

# Shared outbound rules for both task groups: AWS endpoints, ECR layers in S3, and EFS.
locals {
  task_groups = {
    service = aws_security_group.service.id
    admin   = aws_security_group.admin.id
  }
}

resource "aws_vpc_security_group_egress_rule" "task_endpoints" {
  for_each = local.task_groups

  security_group_id            = each.value
  description                  = "HTTPS to the VPC interface endpoints"
  referenced_security_group_id = aws_security_group.endpoints.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}

resource "aws_vpc_security_group_egress_rule" "task_s3" {
  for_each = local.task_groups

  security_group_id = each.value
  description       = "HTTPS to S3 through the gateway endpoint (ECR image layers)"
  prefix_list_id    = aws_vpc_endpoint.s3.prefix_list_id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "task_efs" {
  for_each = local.task_groups

  security_group_id            = each.value
  description                  = "NFS to the EFS mount targets"
  referenced_security_group_id = aws_security_group.efs.id
  ip_protocol                  = "tcp"
  from_port                    = 2049
  to_port                      = 2049
}

resource "aws_security_group" "endpoints" {
  name        = "${var.name}-endpoints"
  description = "VPC interface endpoints: HTTPS in from the tasks only"
  vpc_id      = aws_vpc.this.id
}

resource "aws_vpc_security_group_ingress_rule" "endpoints_from_tasks" {
  for_each = local.task_groups

  security_group_id            = aws_security_group.endpoints.id
  description                  = "HTTPS from the ${each.key} tasks"
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}

resource "aws_security_group" "efs" {
  name        = "${var.name}-efs"
  description = "EFS mount targets: NFS in from the tasks only"
  vpc_id      = aws_vpc.this.id
}

resource "aws_vpc_security_group_ingress_rule" "efs_from_tasks" {
  for_each = local.task_groups

  security_group_id            = aws_security_group.efs.id
  description                  = "NFS from the ${each.key} tasks"
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 2049
  to_port                      = 2049
}
