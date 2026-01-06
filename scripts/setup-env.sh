#!/bin/bash
#
# Copyright The Linux Foundation and each contributor to LFX.
# SPDX-License-Identifier: MIT
#
# Script to set up environment variables for LFX v2 mock data tool
# This script retrieves NATS URL, OpenFGA Store ID, and JWT RSA secret
#
# Usage: source ./scripts/setup-env.sh
#        or
#        eval "$(./scripts/setup-env.sh)"

NAMESPACE="lfx"

echo "=========================================" >&2
echo "  LFX Environment Setup" >&2
echo "=========================================" >&2
echo "" >&2

# Set NATS URL
echo "📡 Setting NATS configuration..." >&2
NATS_URL="lfx-platform-nats.lfx.svc.cluster.local:4222"
echo "export NATS_URL=\"$NATS_URL\""
echo "  ✓ NATS_URL: $NATS_URL" >&2
echo "" >&2

# Get OpenFGA Store ID
echo "🔐 Retrieving OpenFGA Store ID..." >&2
OPENFGA_RESPONSE=$(curl -s "http://lfx-platform-openfga.lfx.svc.cluster.local:8080/stores" 2>/dev/null)
CURL_EXIT_CODE=$?

if [ $CURL_EXIT_CODE -ne 0 ] || [ -z "$OPENFGA_RESPONSE" ]; then
	echo "  ⚠️  Failed to retrieve OpenFGA stores" >&2
	echo "  Please check that OpenFGA is running and accessible" >&2
	echo "" >&2
else
	# Extract the first store ID from the JSON response
	OPENFGA_STORE_ID=$(echo "$OPENFGA_RESPONSE" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)

	if [ -z "$OPENFGA_STORE_ID" ]; then
		echo "  ⚠️  No OpenFGA store found in response" >&2
		echo "  Response: $OPENFGA_RESPONSE" >&2
		echo "" >&2
	else
		echo "export OPENFGA_STORE_ID=\"$OPENFGA_STORE_ID\""
		echo "  ✓ OPENFGA_STORE_ID: $OPENFGA_STORE_ID" >&2
		echo "" >&2
	fi
fi

# Get JWT RSA secret
echo "🔑 Retrieving JWT RSA secret..." >&2
JWT_RSA_SECRET=$(kubectl get secret/heimdall-signer-cert -n "$NAMESPACE" -o json 2>/dev/null | jq -r '.data["signer.pem"]' 2>/dev/null | base64 --decode 2>/dev/null)
KUBECTL_EXIT_CODE=$?

if [ $KUBECTL_EXIT_CODE -ne 0 ] || [ -z "$JWT_RSA_SECRET" ]; then
	echo "  ⚠️  Failed to retrieve JWT RSA secret from heimdall-signer-cert" >&2
	echo "  Please check that kubectl is configured and the secret exists" >&2
	echo "" >&2
else
	# Output the JWT secret (this will be captured by eval or source)
	echo "export JWT_RSA_SECRET='$JWT_RSA_SECRET'"
	echo "  ✓ JWT_RSA_SECRET retrieved successfully" >&2
	echo "" >&2
fi

echo "=========================================" >&2
echo "✅ Environment setup complete!" >&2
echo "=========================================" >&2
echo "" >&2
echo "To use these variables in your current shell:" >&2
echo "  source ./scripts/setup-env.sh" >&2
echo "  or" >&2
echo "  eval \"\$(./scripts/setup-env.sh)\"" >&2
echo "" >&2
echo "Then run the mock data tool:" >&2
echo "  uv run lfx-v2-mockdata \\" >&2
echo "    --jwt-rsa-secret \"\$JWT_RSA_SECRET\" \\" >&2
echo "    -t playbooks/projects/{root_project_access,base_projects,extra_projects} playbooks/committees/base_committees" >&2
echo "" >&2
