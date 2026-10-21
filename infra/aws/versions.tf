# A blueprint, validated in CI and never applied by this repo: no AWS account, no running
# cost, no public endpoint. An adopter applies it in their own HIPAA-eligible account, under
# their own BAA with AWS. State backend: configure your own (an S3 bucket with KMS and a
# lock), see README.md. No secret value is ever passed to Terraform, so state holds none.
terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.68"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = merge(var.tags, {
      Project   = var.name
      ManagedBy = "terraform"
      DataClass = "phi"
    })
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  region     = data.aws_region.current.region

  # The image's unprivileged user (Dockerfile: useradd --uid 10001). EFS access points and
  # the task definition run as it; tests/test_infra.py checks the two stay the same.
  app_uid = 10001
  port    = 8000
}
