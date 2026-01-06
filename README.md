# LFX v2 platform mock data loader

## Overview

This tool generates mock data for the LFX v2 platform by running playbooks that populate projects, committees, and other resources through API calls, NATS requests, and direct NATS KV operations.

## Prerequisites

- Python 3.12 (managed automatically by uv)
- Local LFX v2 platform running [via Helm](https://github.com/linuxfoundation/lfx-v2-helm/tree/main/charts/lfx-platform#readme)
- `uv` package manager installed
- `jwt` CLI from [jwt-cli](https://github.com/mike-engel/jwt-cli) Rust crate available in your $PATH

These instructions and playbooks assume the script's execution environment has access to `*.*.svc.cluster.local` Kubernetes service URLs. These URLs in the playbooks can be overridden with environmental variables as needed.

## Setup

### Quick Setup (Recommended)

Use the automated setup script to configure all environment variables:

```bash
# Source the script to set environment variables in your current shell
source ./scripts/setup-env.sh
```

The script will automatically:

- Set `NATS_URL` to the default Kubernetes service URL
- Retrieve and set `OPENFGA_STORE_ID` from the OpenFGA API
- Retrieve and set `JWT_RSA_SECRET` from the heimdall-signer-cert secret

After running this script, you can proceed directly to [Running Mock Data Generation](#running-mock-data-generation).

### Manual Setup (Alternative)

If you prefer to set environment variables manually or need to customize values:

#### 1. Set Environment Variables

##### NATS Configuration

```bash
export NATS_URL="lfx-platform-nats.lfx.svc.cluster.local:4222"
```

##### OpenFGA Configuration

First, confirm the OpenFGA Store ID:

```bash
curl -sSi "http://lfx-platform-openfga.lfx.svc.cluster.local:8080/stores"
```

Then export the Store ID:

```bash
export OPENFGA_STORE_ID="your-store-id-here"
```

##### Authentication Tokens

A Heimdall JWT secret is needed to use the `!jwt` macro in playbooks. Export it as an environmental variable so you can pass it to the mock data tool as a command line argument:

```bash
export JWT_RSA_SECRET="$(kubectl get secret/heimdall-signer-cert -n lfx -o json | jq -r '.data["signer.pem"]' | base64 --decode)"
```

## Usage

### Running Mock Data Generation

Use uv to run the mock data tool (uv will automatically manage Python versions and virtual environments):

```bash
# Test the script (uv will create the virtual environment automatically).
uv run lfx-v2-mockdata --help

# Load some data!
uv run lfx-v2-mockdata \
    --jwt-rsa-secret "$JWT_RSA_SECRET" \
    -t playbooks/projects/{root_project_access,base_projects,extra_projects} playbooks/committees/base_committees
```

**Important Notes:**

- **Order matters!** Playbook directories run in the order specified on the command line.
- Within each directory, playbooks execute in alphabetical order.
- Dependencies between playbooks should be considered when organizing execution order. Multiple passes are made to allow `!ref` calls to be resolved, but the right order will improve performance and help avoid max-retry errors.
- The `!jwt` macro will attempt to detect the JWKS key ID from the endpoint at `http://lfx-platform-heimdall.lfx.svc.cluster.local:4457/.well-known/jwks`. If this URL is not accessible from the execution environment, you must pass an explicit JWT key ID using the `--jwt-key-id` argument.

### Wiping Existing Data

If you need to start fresh, use the reset script for a complete data wipe:

```bash
./scripts/reset-data.sh
```

This script will:

- Clear all NATS KV buckets (projects, committees, meetings, etc.)
- Clear and recreate OpenSearch indices (using current mapping)
- Restart the query service to clear cache
- Delete the project service pod to clear cache

**Safety Features:**
- Requires typing `RESET` to confirm before proceeding
- Validates all critical operations and exits on failure
- Preserves authentication data in `authelia-users` and `authelia-email-otp` buckets
- Automatically retrieves and uses current OpenSearch mapping before recreation

**Manual Alternative:** If you prefer to wipe only NATS KV buckets manually:

```bash
for bucket in projects project-settings committees committee-settings committee-members; do
    nats kv rm -f $bucket
    nats kv add $bucket
done
```

_Note: The reset script is the recommended approach as it handles OpenSearch indices and service caches comprehensively._

### Running After Data Wipe

After using the reset script, the ROOT project is automatically recreated by the project service pod restart. You can run the mock data tool normally:

```bash
uv run lfx-v2-mockdata \
    --jwt-rsa-secret "$JWT_RSA_SECRET" \
    -t playbooks/projects/{root_project_access,base_projects,extra_projects} playbooks/committees/base_committees
```

**Note:** If you wiped data manually (without the reset script), you'll need to delete the project service pod to trigger ROOT project recreation, as it handles permissions correctly:

```bash
kubectl delete pod -n lfx $(kubectl get pods -n lfx --no-headers | grep project-service | awk '{print $1}')
```

## Playbook Structure

The playbooks are organized by service type, to allow only loading data for the services you have in your environment.

Please refer to the comments in the YAML files for more information on each playbook's role and purpose.
