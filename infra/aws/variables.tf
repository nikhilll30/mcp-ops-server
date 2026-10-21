variable "name" {
  description = "Prefix for every resource name."
  type        = string
  default     = "mcp-ops"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,19}$", var.name))
    error_message = "name must be 2-20 characters: lower-case letters, digits and hyphens, starting with a letter."
  }
}

variable "region" {
  description = "AWS region. Use one where every service in hipaa-services.json is HIPAA-eligible for your account."
  type        = string
  default     = "us-east-1"
}

variable "tags" {
  description = "Extra tags for every resource."
  type        = map(string)
  default     = {}
}

variable "vpc_cidr" {
  description = "CIDR of the new VPC. It has no internet gateway and no NAT: reach it through your own VPN, peering or Transit Gateway."
  type        = string
  default     = "10.40.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0)) && tonumber(split("/", var.vpc_cidr)[1]) <= 20
    error_message = "vpc_cidr must be a valid IPv4 CIDR of /20 or larger."
  }
}

variable "availability_zone_ids" {
  description = "Zone IDs (not names, which differ per account) for the subnets, EFS mount targets and load balancer. Defaults are in us-east-1."
  type        = list(string)
  default     = ["use1-az1", "use1-az2"]

  validation {
    condition     = length(var.availability_zone_ids) >= 2 && length(var.availability_zone_ids) <= 3 && length(distinct(var.availability_zone_ids)) == length(var.availability_zone_ids)
    error_message = "availability_zone_ids must list 2 or 3 distinct zone IDs (the load balancer needs at least 2)."
  }
}

variable "image_digest" {
  description = "Digest of the image in this blueprint's ECR repository (copy the published, CI-tested image there first). Tags are refused: a deployment must name exactly one image."
  type        = string

  validation {
    condition     = can(regex("^sha256:[0-9a-f]{64}$", var.image_digest))
    error_message = "image_digest must be sha256:<64 hex characters>, never a tag."
  }
}

variable "certificate_arn" {
  description = "ACM certificate for the load balancer's HTTPS listener (your domain, issued in your account)."
  type        = string

  validation {
    condition     = can(regex("^arn:aws[a-z-]*:acm:[a-z0-9-]+:[0-9]{12}:certificate/.+$", var.certificate_arn))
    error_message = "certificate_arn must be an ACM certificate ARN."
  }
}

variable "allowed_ingress_cidrs" {
  description = "Networks allowed to reach the internal load balancer on 443 (your VPN or office ranges, as seen inside the VPC)."
  type        = list(string)

  validation {
    condition     = length(var.allowed_ingress_cidrs) > 0 && alltrue([for c in var.allowed_ingress_cidrs : can(cidrhost(c, 0))])
    error_message = "allowed_ingress_cidrs must be a non-empty list of valid CIDRs."
  }

  validation {
    condition     = alltrue([for c in var.allowed_ingress_cidrs : tonumber(split("/", c)[1]) >= 8])
    error_message = "allowed_ingress_cidrs must not include 0.0.0.0/0 or any range wider than /8: this blueprint is not an internet endpoint."
  }
}

variable "backend" {
  description = "\"fhir\" (the server fronts your FHIR R4 system) or \"sqlite\" (the synthetic demo data, in an encrypted file on EFS)."
  type        = string
  default     = "fhir"

  validation {
    condition     = contains(["fhir", "sqlite"], var.backend)
    error_message = "backend must be \"fhir\" or \"sqlite\"."
  }
}

variable "fhir_url" {
  description = "FHIR R4 base URL (https), reachable from the private subnets. Required when backend is \"fhir\"."
  type        = string
  default     = ""

  validation {
    condition     = var.backend != "fhir" || can(regex("^https://[^/:]+(:443)?(/.*)?$", var.fhir_url))
    error_message = "With backend = \"fhir\", fhir_url must be an https:// URL on port 443 (TLS in transit to the record system)."
  }
}

variable "fhir_cidrs" {
  description = "Where the FHIR server lives. The tasks may open TCP 443 to these ranges and nowhere else outside the VPC."
  type        = list(string)
  default     = []

  validation {
    condition     = var.backend != "fhir" || (length(var.fhir_cidrs) > 0 && alltrue([for c in var.fhir_cidrs : can(cidrhost(c, 0)) && tonumber(split("/", c)[1]) >= 16]))
    error_message = "With backend = \"fhir\", fhir_cidrs must list the FHIR server's ranges, each /16 or narrower."
  }
}

variable "audience" {
  description = "The audience this server accepts (MCP_OPS_AUDIENCE). Empty means the server's default, mcp-ops-server."
  type        = string
  default     = ""
}

variable "jwt_issuer" {
  description = "OAuth 2.1 issuer whose JWTs the server accepts (MCP_OPS_JWT_ISSUER). Empty: the tokens file only."
  type        = string
  default     = ""
}

variable "jwt_jwks_file" {
  description = "Name of the issuer's JWKS file in the data volume (e.g. jwks.json). A JWKS URL would need internet egress, which this VPC does not have."
  type        = string
  default     = "jwks.json"

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]+\\.json$", var.jwt_jwks_file))
    error_message = "jwt_jwks_file must be a plain file name ending in .json (it lives in the data volume), never a URL."
  }
}

variable "rate_limit_per_minute" {
  description = "Tool calls per client per minute, per task (MCP_OPS_RATE_LIMIT_PER_MIN)."
  type        = number
  default     = 120
}

variable "waf_rate_limit_per_5_min" {
  description = "Requests per source IP per 5 minutes before the WAF blocks it (across all tasks, unlike the server's own limit)."
  type        = number
  default     = 2000

  validation {
    condition     = var.waf_rate_limit_per_5_min >= 100
    error_message = "waf_rate_limit_per_5_min must be at least 100 (the WAF minimum)."
  }
}

variable "log_retention_days" {
  description = "Retention for container, flow, WAF and load balancer logs. Your retention policy decides; at least a year here."
  type        = number
  default     = 400

  validation {
    condition     = contains([400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.log_retention_days)
    error_message = "log_retention_days must be a CloudWatch Logs retention value of at least 400 days."
  }
}

variable "start_service" {
  description = "Run the service's task. Leave false for the first apply: the image, the data key and a tokens file must exist first (README.md), or the first deployment fails and stops."
  type        = bool
  default     = false
}

variable "cpu" {
  description = "Fargate task CPU units."
  type        = number
  default     = 512
}

variable "memory" {
  description = "Fargate task memory (MiB)."
  type        = number
  default     = 1024
}
