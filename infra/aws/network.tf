# Private networking only. No internet gateway, no NAT, no public IPs: tasks reach AWS APIs
# through VPC endpoints, the FHIR server through fhir_cidrs, and nothing else.
# scripts/check_infra.py fails CI if an internet gateway, NAT or Elastic IP ever appears.

locals {
  az_count = length(var.availability_zone_ids)

  # Interface endpoints a Fargate task in a private subnet needs: image pull (ECR API and
  # registry), container logs, and the data key from Secrets Manager.
  interface_endpoints = toset(["ecr.api", "ecr.dkr", "logs", "secretsmanager"])
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true # required for the endpoints' private DNS names

  tags = { Name = var.name }
}

# The VPC's default security group, emptied: nothing may use it by accident.
resource "aws_default_security_group" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name}-default-unused" }
}

resource "aws_subnet" "private" {
  count = local.az_count

  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone_id    = var.availability_zone_ids[count.index]
  map_public_ip_on_launch = false

  tags = { Name = "${var.name}-private-${var.availability_zone_ids[count.index]}" }
}

# Only the implicit local route, plus the S3 gateway endpoint's prefix list.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name}-private" }
}

resource "aws_route_table_association" "private" {
  count = local.az_count

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

resource "aws_vpc_endpoint" "interface" {
  for_each = local.interface_endpoints

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${local.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]

  tags = { Name = "${var.name}-${each.key}" }
}

# ECR stores image layers in S3. The endpoint policy allows reading those layers only, so
# the endpoint cannot be used to move data to any other bucket.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${local.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "EcrLayersOnly"
      Effect    = "Allow"
      Principal = "*"
      Action    = ["s3:GetObject"]
      Resource  = ["arn:${local.partition}:s3:::prod-${local.region}-starport-layer-bucket/*"]
    }]
  })

  tags = { Name = "${var.name}-s3" }
}

# Flow logs: every accepted and rejected connection, encrypted, kept as long as the app's logs.
resource "aws_cloudwatch_log_group" "flow" {
  name              = "/${var.name}/vpc-flow"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.logs.arn
}

resource "aws_flow_log" "this" {
  vpc_id               = aws_vpc.this.id
  traffic_type         = "ALL"
  log_destination_type = "cloud-watch-logs"
  log_destination      = aws_cloudwatch_log_group.flow.arn
  iam_role_arn         = aws_iam_role.flow_logs.arn
}
