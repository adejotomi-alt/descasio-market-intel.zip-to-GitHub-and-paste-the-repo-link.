#!/usr/bin/env bash
# Descasio Market Intelligence Hub — Lambda Deployment Script
# Usage: ./deploy/deploy.sh [--env dev|prod] [--region eu-west-1]
# Requires: AWS CLI configured with appropriate permissions

set -euo pipefail

# ─── Configuration ────────────────────────────────────────────────────────────
FUNCTION_NAME="descasio-market-intel"
RUNTIME="python3.12"
HANDLER="deploy.lambda_handler.handler"
TIMEOUT=300
MEMORY=512
ARCH="arm64"
REGION="${AWS_REGION:-eu-west-1}"
ROLE_ARN="${LAMBDA_ROLE_ARN:-}"
BUILD_DIR="/tmp/descasio-intel-build"
ZIP_FILE="/tmp/descasio-intel.zip"

ENV="${1:-prod}"
echo "🚀 Deploying Descasio Market Intelligence Hub"
echo "   Function: ${FUNCTION_NAME}"
echo "   Region:   ${REGION}"
echo "   Env:      ${ENV}"
echo ""

# ─── Pre-flight Checks ────────────────────────────────────────────────────────
check_dependencies() {
  echo "▸ Checking dependencies..."
  command -v aws >/dev/null 2>&1 || { echo "ERROR: AWS CLI not found. Install: https://aws.amazon.com/cli/"; exit 1; }
  command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found."; exit 1; }
  command -v pip3 >/dev/null 2>&1 || { echo "ERROR: pip3 not found."; exit 1; }
  aws sts get-caller-identity --query "Arn" --output text >/dev/null 2>&1 || { echo "ERROR: AWS CLI not authenticated. Run: aws configure"; exit 1; }
  echo "  ✓ All dependencies present"
}

# ─── Build Package ────────────────────────────────────────────────────────────
build_package() {
  echo "▸ Building deployment package..."
  rm -rf "${BUILD_DIR}"
  mkdir -p "${BUILD_DIR}"

  # Install dependencies into build dir
  pip3 install -r requirements.txt -t "${BUILD_DIR}" --quiet \
    --platform manylinux2014_aarch64 \
    --implementation cp \
    --python-version 3.12 \
    --only-binary=:all: \
    --upgrade

  # Install Playwright (needs special handling)
  pip3 install playwright -t "${BUILD_DIR}" --quiet

  # Copy source code
  cp -r config ingestion processing delivery orchestrator deploy "${BUILD_DIR}/"

  # Remove test files and __pycache__ to keep package lean
  find "${BUILD_DIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
  find "${BUILD_DIR}" -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
  find "${BUILD_DIR}" -name "*.pyc" -delete 2>/dev/null || true
  find "${BUILD_DIR}" -name "*.pyo" -delete 2>/dev/null || true

  # Create ZIP
  cd "${BUILD_DIR}"
  zip -r9 "${ZIP_FILE}" . --quiet
  cd -

  SIZE_MB=$(du -sm "${ZIP_FILE}" | cut -f1)
  echo "  ✓ Package built: ${ZIP_FILE} (${SIZE_MB}MB)"

  if [ "${SIZE_MB}" -gt 250 ]; then
    echo "  ⚠ Package exceeds 250MB — consider using Lambda layers for dependencies"
  fi
}

# ─── Deploy or Update Function ─────────────────────────────────────────────────
deploy_function() {
  echo "▸ Checking if function exists..."

  if aws lambda get-function --function-name "${FUNCTION_NAME}" --region "${REGION}" >/dev/null 2>&1; then
    echo "  Function exists — updating code..."
    aws lambda update-function-code \
      --function-name "${FUNCTION_NAME}" \
      --zip-file "fileb://${ZIP_FILE}" \
      --architectures "${ARCH}" \
      --region "${REGION}" \
      --output table

    echo "  Waiting for update to complete..."
    aws lambda wait function-updated \
      --function-name "${FUNCTION_NAME}" \
      --region "${REGION}"

    echo "  Updating configuration..."
    aws lambda update-function-configuration \
      --function-name "${FUNCTION_NAME}" \
      --runtime "${RUNTIME}" \
      --handler "${HANDLER}" \
      --timeout "${TIMEOUT}" \
      --memory-size "${MEMORY}" \
      --region "${REGION}" \
      --output text --query "FunctionArn"

  else
    echo "  Function does not exist — creating..."
    if [ -z "${ROLE_ARN}" ]; then
      echo "ERROR: LAMBDA_ROLE_ARN environment variable required for first-time deployment."
      echo "Create an IAM role with: AWSLambdaBasicExecutionRole + ses:SendEmail + secretsmanager:GetSecretValue"
      exit 1
    fi

    aws lambda create-function \
      --function-name "${FUNCTION_NAME}" \
      --runtime "${RUNTIME}" \
      --handler "${HANDLER}" \
      --timeout "${TIMEOUT}" \
      --memory-size "${MEMORY}" \
      --architectures "${ARCH}" \
      --role "${ROLE_ARN}" \
      --zip-file "fileb://${ZIP_FILE}" \
      --region "${REGION}" \
      --description "Descasio Market Intelligence Hub — Autonomous pan-African market tracking" \
      --output table
  fi

  echo "  ✓ Function deployed"
}

# ─── Set Environment Variables ─────────────────────────────────────────────────
configure_env() {
  echo "▸ Configuring environment variables..."

  # Load from .env file if present
  ENV_VARS=""
  if [ -f ".env" ]; then
    while IFS= read -r line; do
      # Skip comments and empty lines
      [[ "$line" =~ ^#.*$ ]] && continue
      [[ -z "$line" ]] && continue
      KEY="${line%%=*}"
      VAL="${line#*=}"
      if [ -n "${KEY}" ] && [ -n "${VAL}" ]; then
        ENV_VARS="${ENV_VARS}${KEY}=${VAL},"
      fi
    done < ".env"
    ENV_VARS="${ENV_VARS%,}"  # Remove trailing comma

    aws lambda update-function-configuration \
      --function-name "${FUNCTION_NAME}" \
      --environment "Variables={${ENV_VARS}}" \
      --region "${REGION}" \
      --output text --query "FunctionArn" >/dev/null

    echo "  ✓ Environment variables set from .env"
  else
    echo "  ⚠ No .env file found — set environment variables manually in AWS Console"
    echo "    Required: ANTHROPIC_API_KEY, ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET,"
    echo "              ZOHO_REFRESH_TOKEN, SLACK_SALES_WEBHOOK, SLACK_EXEC_WEBHOOK"
  fi
}

# ─── Create / Update EventBridge Rules ─────────────────────────────────────────
setup_eventbridge() {
  echo "▸ Setting up EventBridge schedules..."

  FUNCTION_ARN=$(aws lambda get-function-configuration \
    --function-name "${FUNCTION_NAME}" \
    --region "${REGION}" \
    --query "FunctionArn" \
    --output text)

  # Sales cycle — every 6 hours
  SALES_RULE=$(aws events put-rule \
    --name "descasio-intel-sales-cycle" \
    --schedule-expression "cron(0 */6 * * ? *)" \
    --description "Descasio Intel Hub — Sales intelligence scan every 6 hours" \
    --state ENABLED \
    --region "${REGION}" \
    --query "RuleArn" \
    --output text)
  echo "  ✓ Sales cycle rule: ${SALES_RULE}"

  aws events put-targets \
    --rule "descasio-intel-sales-cycle" \
    --targets "Id=descasio-sales-lambda,Arn=${FUNCTION_ARN},Input={\"mode\":\"sales\"}" \
    --region "${REGION}" \
    --output text >/dev/null

  # Grant EventBridge permission to invoke Lambda
  aws lambda add-permission \
    --function-name "${FUNCTION_NAME}" \
    --statement-id "EventBridgeSalesSchedule" \
    --action "lambda:InvokeFunction" \
    --principal "events.amazonaws.com" \
    --source-arn "${SALES_RULE}" \
    --region "${REGION}" \
    --output text >/dev/null 2>/dev/null || true

  # Exec briefing — 1st of month 05:00 UTC
  EXEC_RULE=$(aws events put-rule \
    --name "descasio-intel-exec-briefing" \
    --schedule-expression "cron(0 5 1 * ? *)" \
    --description "Descasio Intel Hub — Monthly C-Suite executive briefing" \
    --state ENABLED \
    --region "${REGION}" \
    --query "RuleArn" \
    --output text)
  echo "  ✓ Exec briefing rule: ${EXEC_RULE}"

  aws events put-targets \
    --rule "descasio-intel-exec-briefing" \
    --targets "Id=descasio-exec-lambda,Arn=${FUNCTION_ARN},Input={\"mode\":\"exec\"}" \
    --region "${REGION}" \
    --output text >/dev/null

  aws lambda add-permission \
    --function-name "${FUNCTION_NAME}" \
    --statement-id "EventBridgeExecSchedule" \
    --action "lambda:InvokeFunction" \
    --principal "events.amazonaws.com" \
    --source-arn "${EXEC_RULE}" \
    --region "${REGION}" \
    --output text >/dev/null 2>/dev/null || true

  echo "  ✓ EventBridge schedules configured"
}

# ─── Smoke Test ───────────────────────────────────────────────────────────────
smoke_test() {
  echo "▸ Running post-deployment smoke test..."

  RESULT=$(aws lambda invoke \
    --function-name "${FUNCTION_NAME}" \
    --payload '{"mode": "validate"}' \
    --region "${REGION}" \
    /tmp/descasio-test-output.json \
    --query "StatusCode" \
    --output text 2>&1)

  if [ "${RESULT}" = "200" ]; then
    STATUS=$(cat /tmp/descasio-test-output.json | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('body','{}'))" 2>/dev/null)
    echo "  ✓ Lambda responds — Status 200"
    echo "  Response: ${STATUS}"
  else
    echo "  ✗ Smoke test failed — Status: ${RESULT}"
    cat /tmp/descasio-test-output.json
  fi
}

# ─── Main ─────────────────────────────────────────────────────────────────────
main() {
  check_dependencies
  build_package
  deploy_function
  configure_env
  setup_eventbridge
  smoke_test

  echo ""
  echo "════════════════════════════════════════════════════════════════"
  echo "✓ Descasio Market Intelligence Hub deployed successfully"
  echo ""
  echo "  Function:          ${FUNCTION_NAME}"
  echo "  Region:            ${REGION}"
  echo "  Sales cycle:       Every 6 hours (EventBridge)"
  echo "  Exec briefing:     1st of month, 05:00 UTC (EventBridge)"
  echo ""
  echo "  Manual test (sales): aws lambda invoke --function-name ${FUNCTION_NAME} --payload '{\"mode\":\"sales\"}' /tmp/out.json"
  echo "  Manual test (exec):  aws lambda invoke --function-name ${FUNCTION_NAME} --payload '{\"mode\":\"exec\"}' /tmp/out.json"
  echo "  Logs:                aws logs tail /aws/lambda/${FUNCTION_NAME} --follow --region ${REGION}"
  echo "════════════════════════════════════════════════════════════════"
}

main "$@"
