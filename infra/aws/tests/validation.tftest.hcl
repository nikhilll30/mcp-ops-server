# Inputs the blueprint must refuse at plan time, each for its own reason.

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

run "refuses_the_internet_as_ingress" {
  command = plan
  variables {
    allowed_ingress_cidrs = ["10.0.0.0/16", "0.0.0.0/0"]
  }
  expect_failures = [var.allowed_ingress_cidrs]
}

run "refuses_an_image_tag" {
  command = plan
  variables {
    image_digest = "latest"
  }
  expect_failures = [var.image_digest]
}

run "refuses_plain_http_to_fhir" {
  command = plan
  variables {
    fhir_url = "http://fhir.internal.example/fhir"
  }
  expect_failures = [var.fhir_url]
}

run "refuses_fhir_without_a_url" {
  command = plan
  variables {
    fhir_url = ""
  }
  expect_failures = [var.fhir_url]
}

run "refuses_fhir_without_a_network" {
  command = plan
  variables {
    fhir_cidrs = []
  }
  expect_failures = [var.fhir_cidrs]
}

run "refuses_a_jwks_url" {
  command = plan
  variables {
    jwt_jwks_file = "https://idp.example/.well-known/jwks.json"
  }
  expect_failures = [var.jwt_jwks_file]
}

run "refuses_short_log_retention" {
  command = plan
  variables {
    log_retention_days = 30
  }
  expect_failures = [var.log_retention_days]
}

run "refuses_a_single_zone" {
  command = plan
  variables {
    availability_zone_ids = ["use1-az1"]
  }
  expect_failures = [var.availability_zone_ids]
}

run "refuses_a_fractional_rate_limit" {
  command = plan
  variables {
    rate_limit_per_minute = 120.5
  }
  expect_failures = [var.rate_limit_per_minute]
}

run "refuses_a_zero_rate_limit" {
  command = plan
  variables {
    rate_limit_per_minute = 0
  }
  expect_failures = [var.rate_limit_per_minute]
}
