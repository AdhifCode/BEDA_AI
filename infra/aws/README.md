# AWS Infrastructure as Code Reference (Terraform)

This directory contains production-ready Terraform definitions for the BEDA Enquiry Intelligence & CRM Automation System.

## Architecture

- **Networking**: VPC with public/private subnets across 2 Availability Zones, NAT Gateways, and isolated database subnets.
- **Compute**: AWS ECS Fargate running:
  - `beda-api` (behind Application Load Balancer with HTTPS termination)
  - `beda-worker` (background queue consumer)
- **Data Persistence**:
  - AWS RDS PostgreSQL 16 (Multi-AZ in production)
  - AWS ElastiCache for Redis (Redis Streams queue)
  - AWS S3 Bucket for attachment storage (server-side encryption via KMS)
- **Security & Secrets**:
  - AWS Secrets Manager for database passwords and third-party API credentials
  - Least-privilege IAM task execution and task roles
- **Observability**:
  - CloudWatch Log Groups for API and Worker
  - Alarms for 5xx errors, worker step retries, and dead-letter queue backlog.

## Deployment Instructions

```bash
cd infra/aws/terraform
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```
