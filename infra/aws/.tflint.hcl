# tflint: the Terraform language rules (recommended preset plus naming and docs), and the
# AWS ruleset for values the provider would accept but AWS would reject at apply time.
config {
  call_module_type = "all"
}

plugin "terraform" {
  enabled = true
  preset  = "all"
}

plugin "aws" {
  enabled = true
  version = "0.49.0"
  source  = "github.com/terraform-linters/tflint-ruleset-aws"
}
