"""
Descasio Market Intelligence — AWS Lambda Handler
Serverless entry point for deployment on AWS Lambda + EventBridge.

EventBridge Schedule Rules (create in AWS Console or via Terraform):
  Sales cycle:    cron(0 */6 * * ? *)    → Every 6 hours
  Exec briefing:  cron(0 5 1 * ? *)      → 1st of month at 05:00 UTC (06:00 WAT)

Required Lambda environment variables (set in Lambda config, not .env):
  Same variables as .env.example — set them in the Lambda function's
  Environment Variables section or pull from AWS Secrets Manager (recommended).

Recommended Lambda config:
  Runtime: python3.12
  Memory:  512 MB
  Timeout: 300 seconds (5 minutes)
  Architecture: arm64 (Graviton — 20% cheaper, same performance)

IAM permissions required:
  - ses:SendEmail (for exec briefing)
  - secretsmanager:GetSecretValue (if using Secrets Manager)
  - logs:CreateLogGroup, logs:CreateLogStream, logs:PutLogEvents
"""

import asyncio
import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: dict, context) -> dict:
    """
    AWS Lambda entry point.
    
    EventBridge event structure:
    {
        "source": "aws.events",
        "detail-type": "Scheduled Event",
        "detail": {},
        "resources": ["arn:aws:events:...rule/descasio-intel-sales-cycle"]
    }
    
    The pipeline mode is inferred from the EventBridge rule name,
    or can be passed directly as event["mode"] for manual invocations.
    """
    # Import here to avoid cold-start overhead for validation-only calls
    from config.settings import Config
    from orchestrator.pipeline import DescasioIntelPipeline

    config = Config()
    pipeline = DescasioIntelPipeline(config)

    # Determine mode from event or EventBridge rule name
    mode = _detect_mode(event)
    logger.info(f"Lambda invoked in mode: {mode}")

    # Validate credentials before running
    missing = config.validate()
    if missing:
        msg = f"Missing environment variables: {', '.join(missing)}"
        logger.error(msg)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": msg, "missing_vars": missing}),
        }

    try:
        if mode == "sales":
            count = asyncio.run(pipeline.run_sales_cycle())
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "mode": "sales",
                    "signals_delivered": count,
                    "status": "success",
                }),
            }

        elif mode == "exec":
            success = asyncio.run(pipeline.run_exec_briefing())
            return {
                "statusCode": 200 if success else 500,
                "body": json.dumps({
                    "mode": "exec",
                    "status": "success" if success else "delivery_failed",
                }),
            }

        else:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": f"Unknown mode: {mode}"}),
            }

    except Exception as e:
        logger.error(f"Pipeline error: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e), "mode": mode}),
        }


def _detect_mode(event: dict) -> str:
    """
    Determine execution mode from:
    1. Direct event['mode'] key (manual / testing invocations)
    2. EventBridge rule ARN in event['resources']
    3. Default: 'sales'
    """
    if "mode" in event:
        return event["mode"]

    resources = event.get("resources", [])
    for resource in resources:
        if "exec" in resource.lower() or "briefing" in resource.lower():
            return "exec"
        if "sales" in resource.lower():
            return "sales"

    return "sales"  # Safe default


# ─── TERRAFORM / CDK REFERENCE ────────────────────────────────────────────────
"""
Terraform resources for EventBridge schedule rules (reference only):

resource "aws_cloudwatch_event_rule" "intel_sales_cycle" {
  name                = "descasio-intel-sales-cycle"
  description         = "Trigger Descasio Intel Hub sales cycle every 6 hours"
  schedule_expression = "cron(0 */6 * * ? *)"
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_rule" "intel_exec_briefing" {
  name                = "descasio-intel-exec-briefing"
  description         = "Trigger Descasio Intel Hub monthly exec briefing"
  schedule_expression = "cron(0 5 1 * ? *)"
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "intel_sales_lambda" {
  rule = aws_cloudwatch_event_rule.intel_sales_cycle.name
  arn  = aws_lambda_function.descasio_intel.arn
  input = jsonencode({ mode = "sales" })
}

resource "aws_cloudwatch_event_target" "intel_exec_lambda" {
  rule = aws_cloudwatch_event_rule.intel_exec_briefing.name
  arn  = aws_lambda_function.descasio_intel.arn
  input = jsonencode({ mode = "exec" })
}
"""
