output "load_balancer_dns_name" {
  description = "Internal DNS name; point your domain's record (matching certificate_arn) at it. MCP endpoint: https://<your domain>/mcp"
  value       = aws_lb.this.dns_name
}

output "ecr_repository_url" {
  description = "Copy the CI-tested image here, then set image_digest to its digest."
  value       = aws_ecr_repository.this.repository_url
}

output "data_key_secret_arn" {
  description = "Set its value once, out of band: python -m mcp_ops_kit.crypto new-key."
  value       = aws_secretsmanager_secret.data_key.arn
}

output "cluster_name" {
  description = "ECS cluster running the service and the admin task."
  value       = aws_ecs_cluster.this.name
}

output "admin_task_definition" {
  description = "Run with a command override to issue or revoke tokens, or verify the audit log."
  value       = aws_ecs_task_definition.admin.family
}

output "admin_network" {
  description = "Subnets and security group for aws ecs run-task of the admin task."
  value = {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.admin.id]
  }
}

output "token_audience" {
  description = "Pass as --audience when issuing tokens: the server refuses a tokens file holding another audience's token."
  value       = var.audience == "" ? "mcp-ops-server" : var.audience
}

output "jwks_path" {
  description = "Where the admin task must put the issuer's JWKS (when jwt_issuer is set)."
  value       = "/data/${var.jwt_jwks_file}"
}
