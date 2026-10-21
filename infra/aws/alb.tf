# An internal load balancer: TLS 1.2+ terminates here, with a certificate from ACM. It has
# no public address, because the VPC has no internet gateway. A WAF in front blocks known
# bad inputs and rate-limits per source IP across every task.

resource "aws_lb" "this" {
  #checkov:skip=CKV2_AWS_20:There is no HTTP listener to redirect; the only listener is HTTPS.
  name                       = var.name
  internal                   = true
  load_balancer_type         = "application"
  subnets                    = aws_subnet.private[*].id
  security_groups            = [aws_security_group.alb.id]
  drop_invalid_header_fields = true
  enable_deletion_protection = true

  access_logs {
    bucket  = aws_s3_bucket.alb_logs.id
    prefix  = "alb"
    enabled = true
  }

  depends_on = [aws_s3_bucket_policy.alb_logs]
}

# TLS ends at the load balancer; the hop to the task is HTTP inside the private subnets
# (the server does not terminate TLS). README.md lists this as the adopter's decision.
resource "aws_lb_target_group" "this" {
  #checkov:skip=CKV_AWS_378:The server speaks HTTP only; TLS terminates at this internal load balancer and the target hop stays inside private subnets (README.md, adopter's decision).
  name        = var.name
  port        = local.port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.this.id

  health_check {
    path                = "/healthz"
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-Res-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this.arn
  }
}

resource "aws_wafv2_web_acl" "this" {
  name  = var.name
  scope = "REGIONAL"

  default_action {
    allow {}
  }

  # Request sampling is off on the ACL and every rule: samples keep full headers, and log
  # redaction does not apply to them, so they would hold bearer tokens (Cursor Security,
  # PR #22). Metrics stay on.

  # Log4j and other known-bad request patterns. Not the common rule set: its 8 KB body rule
  # would block MCP requests the server allows (MCP_OPS_MAX_BODY_BYTES is 64 KB).
  rule {
    name     = "known-bad-inputs"
    priority = 1

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-known-bad-inputs"
      sampled_requests_enabled   = false
    }
  }

  # The server's own rate limit is per process; this one holds across every task.
  rule {
    name     = "rate-per-ip"
    priority = 2

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.waf_rate_limit_per_5_min
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-rate-per-ip"
      sampled_requests_enabled   = false
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = var.name
    sampled_requests_enabled   = false
  }
}

resource "aws_wafv2_web_acl_association" "this" {
  resource_arn = aws_lb.this.arn
  web_acl_arn  = aws_wafv2_web_acl.this.arn
}

# WAF logs record request metadata. The Authorization header is redacted, so a bearer token
# never reaches the log (and sampling, which redaction does not cover, is off above).
resource "aws_cloudwatch_log_group" "waf" {
  name              = "aws-waf-logs-${var.name}" # WAF requires this prefix
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.logs.arn
}

resource "aws_wafv2_web_acl_logging_configuration" "this" {
  resource_arn            = aws_wafv2_web_acl.this.arn
  log_destination_configs = [aws_cloudwatch_log_group.waf.arn]

  redacted_fields {
    single_header {
      name = "authorization"
    }
  }
}

# Load balancer access logs. They hold client IPs, paths and status codes, never request
# bodies, so no tool arguments. ELB can write only with SSE-S3, not a KMS key.
resource "aws_s3_bucket" "alb_logs" {
  #checkov:skip=CKV_AWS_144:Cross-region replication is the adopter's backup decision; these logs hold no PHI.
  #checkov:skip=CKV_AWS_18:This is the access-log bucket; logging it to itself would loop.
  #checkov:skip=CKV2_AWS_62:No consumer of event notifications for access logs.
  #checkov:skip=CKV_AWS_145:ELB access logs support only SSE-S3 (AES256), not SSE-KMS.
  bucket_prefix = "${var.name}-alb-logs-"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "alb_logs" {
  bucket                  = aws_s3_bucket.alb_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  rule {
    id     = "expire"
    status = "Enabled"
    filter {}

    expiration {
      days = var.log_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

data "aws_elb_service_account" "this" {}

resource "aws_s3_bucket_policy" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyWithoutTls"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.alb_logs.arn, "${aws_s3_bucket.alb_logs.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        Sid       = "ElbWritesAccessLogs"
        Effect    = "Allow"
        Principal = { AWS = data.aws_elb_service_account.this.arn }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.alb_logs.arn}/alb/AWSLogs/${local.account_id}/*"
      },
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.alb_logs]
}
