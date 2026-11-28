# AWS deployment boundary

The domain/application packages contain no AWS SDK dependencies. Deployment should run the container on ECS/Fargate behind a private network/load balancer, with secrets injected from AWS Secrets Manager and persistent operational data in managed PostgreSQL/S3 as appropriate.

Live broker credentials and live routing remain disabled until a separately approved production phase.
