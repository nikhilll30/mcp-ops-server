# AWS blueprint (Terraform)

A reviewable deployment of the clinic MCP server on AWS services that AWS lists as
HIPAA-eligible. **Validated in CI on every PR and never applied by this repo**: there is no
AWS account behind it, no running cost and no public endpoint. It is **HIPAA-ready
infrastructure, not a HIPAA-compliant system**: compliance needs your BAA with AWS (and
with your LLM provider), your risk analysis, policies and people.

## What it builds

```
 your network (VPN / peering / Transit Gateway), allowed_ingress_cidrs only
   │ HTTPS 443, TLS 1.2+ (ACM certificate)
   ▼
 WAF (known bad inputs, rate limit per IP) ─► internal ALB (access logs to S3)
   │ HTTP 8000, inside private subnets
   ▼
 ECS Fargate service: 1 task, image pinned by digest, uid 10001, read-only root, no
 capabilities, no ECS Exec, no public IP
   ├─ /data   EFS access point, read-only   tokens file (and JWKS / revocations)
   ├─ /audit  EFS access point, read-write  encrypted, hash-chained audit log
   ├─ /state  EFS access point, read-write  encrypted SQLite (backend = "sqlite" only)
   ├─ MCP_OPS_DATA_KEY from Secrets Manager (KMS), never plain environment or state
   └─ HTTPS 443 to fhir_cidrs only ─► your FHIR R4 server
 VPC endpoints: ECR (api, dkr), S3 (gateway; ECR layers only), CloudWatch Logs,
 Secrets Manager. No internet gateway, no NAT, no Elastic IP.
 KMS: one key for logs, one for data, both rotated. Logs kept >= 400 days.
 Admin task (run on demand): issue/revoke tokens, verify the audit log.
```

| Safeguard (45 CFR 164.312) | Where |
|---|---|
| Access control (a)(1): least privilege | `iam.tf`: the server can read the tokens file but not write it; the admin task can write tokens but not the audit log. `storage.tf`: EFS refuses non-TLS, root and any other principal |
| Audit controls (b) | The server's own audit log on `/audit` (Part 9), plus VPC flow logs, WAF logs (Authorization header redacted; request sampling off, since samples are not redacted), ALB access logs, container logs |
| Integrity (c)(1) | The audit log's HMAC chain (Part 9); image pinned by digest in an immutable, scanned ECR repository |
| Transmission security (e)(1) | TLS 1.2+ at the ALB; TLS to EFS and to FHIR (`https://` enforced); private subnets only |
| Encryption at rest (a)(2)(iv) | KMS on EFS, Secrets Manager, ECR and every log group; the audit log and database are also encrypted by the server itself |

## What is still yours

- A **BAA with AWS** covering this account, and confirming every service in
  [`hipaa-services.json`](hipaa-services.json) against
  [AWS's live list](https://aws.amazon.com/compliance/hipaa-eligible-services-reference/).
  That file records how each entry was checked; Amazon S3 is marked `unverified` because
  no source seen while building this named it.
- **TLS between the load balancer and the task.** The server speaks HTTP; TLS ends at the
  internal ALB and the last hop stays inside private subnets. If your policy needs TLS end
  to end, add a TLS sidecar or terminate in the task.
- **CloudTrail, GuardDuty, Config and Security Hub**: account-level, usually run by your
  organisation. Not created here.
- **Network reachability**: the VPC has no internet path. Connect it to your network and
  your FHIR server (peering or Transit Gateway), and set `allowed_ingress_cidrs` and
  `fhir_cidrs`. A JWKS **URL** would need internet egress, so JWT keys come from a file in
  `/data` instead.
- **Terraform state**: configure your own backend (an S3 bucket with KMS and locking). No
  secret value is ever passed to Terraform, but state still names your resources.
- **Running cost** once applied: a load balancer, a Fargate task, EFS, four interface
  endpoints per zone, a WAF and two KMS keys. Price it for your region before applying.
- **One task only.** The audit log and the database each take an exclusive lock, so the
  service runs one task and stops the old one before starting the new (a short gap on
  each deploy). Scaling out needs a shared audit sink first.

## Use it

```bash
cd infra/aws
terraform init                       # with your own backend configuration
terraform plan -var-file=prod.tfvars # image_digest, certificate_arn, allowed_ingress_cidrs, fhir_url, fhir_cidrs
terraform apply -var-file=prod.tfvars
```

Then, once:
1. Copy the CI-tested image (`ghcr.io/nikhilll30/mcp-ops-server`, by digest) into the
   `ecr_repository_url` output, and use that digest as `image_digest`.
2. Set the data key: `aws secretsmanager put-secret-value --secret-id <data_key_secret_arn>
   --secret-string "$(python -m mcp_ops_kit.crypto new-key)"`. Keep no other copy.
3. Make a token **on your own machine** and register only its hash with the admin task, so
   the token never reaches AWS (the admin task's output goes to CloudWatch):
   ```bash
   TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')   # give it to the client
   HASH=$(printf %s "$TOKEN" | sha256sum | cut -d" " -f1)
   ```
   Then `aws ecs run-task` with `admin_task_definition`, the `admin_network` output and the
   command override `python -m mcp_ops_server.auth issue --file /data/tokens.json --client
   portal --scope ... --sha256 $HASH`. It prints only the token ID. Never run plain `issue`
   (without `--sha256`) in the admin task: it prints the token, into a log kept 400 days.
4. With JWTs: put the issuer's JWKS at `/data/jwks.json` and create `/data/revoked.json`
   (`python -m mcp_ops_kit.oauth init --file /data/revoked.json`) the same way.

## How CI checks it

The `terraform` job: `terraform fmt`, `init` against the committed lock file, `validate`,
`terraform test` (17 runs: 36 assertions and 8 refused inputs, against a **mocked** AWS provider: the hardening, the IAM split,
the network and encryption settings, and 8 inputs that must be refused), `tflint` with the
AWS ruleset, `checkov` (every skip carries its reason next to the resource), and
[`scripts/check_infra.py`](../../scripts/check_infra.py): every resource type mapped to a
HIPAA-eligible or no-PHI service, forbidden types (internet gateway, NAT, Elastic IP,
secret values, IAM users), no `0.0.0.0/0`, settings documented in `.env.example`, and the
uid matching the image. The `container` job runs the image with the task definition's
hardening (read-only root, no capabilities).
