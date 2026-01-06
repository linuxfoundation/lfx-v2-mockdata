# LFX v2 platform mock data loader

## Overview

This tool generates mock data for the LFX v2 platform by running playbooks that populate projects, committees, and other resources through API calls, NATS requests, and direct NATS KV operations.

## Prerequisites

- Python 3.12 (managed automatically by uv)
- Local LFX v2 platform running [via Helm](https://github.com/linuxfoundation/lfx-v2-helm/tree/main/charts/lfx-platform#readme)
- `uv` package manager installed
- `jwt` CLI from [jwt-cli](https://github.com/mike-engel/jwt-cli) Rust crate available in your $PATH

These instructions and playbooks assume the script's execution environment has access to `*.*.svc.cluster.local` Kubernetes service URLs. These URLs in the playbooks can be overridden with environmental variables as needed.

## Quick Start

```bash
# Set up environment variables
eval $(./scripts/setup-env.sh)

# Load all standard mock data
make load
```

Run `make help` to see all available commands.

## Setup

### Automated Setup (Recommended)

Use the setup script to configure all environment variables:

```bash
eval $(./scripts/setup-env.sh)
```

The script will automatically:

- Set `NATS_URL` to the default Kubernetes service URL
- Retrieve and set `OPENFGA_STORE_ID` from the OpenFGA API
- Retrieve and set `JWT_RSA_SECRET` from the heimdall-signer-cert secret

Verify your environment is configured:

```bash
make check-env
```

### Manual Setup (Alternative)

If you prefer to set environment variables manually or need to customize values:

```bash
# NATS URL
export NATS_URL="lfx-platform-nats.lfx.svc.cluster.local:4222"

# OpenFGA Store ID (get from API)
curl -sSi "http://lfx-platform-openfga.lfx.svc.cluster.local:8080/stores"
export OPENFGA_STORE_ID="your-store-id-here"

# JWT RSA secret from Heimdall
export JWT_RSA_SECRET="$(kubectl get secret/heimdall-signer-cert -n lfx -o json | jq -r '.data["signer.pem"]' | base64 --decode)"
```

## Usage

### Loading Mock Data

Use the Makefile targets to load data:

```bash
make load              # Load all standard playbooks
make load-projects     # Load only project playbooks
make load-committees   # Load only committee playbooks
make load-mailing-lists # Load mailing list playbooks
make load-meetings     # Load v1 meeting playbooks
```

Or run the tool directly for custom playbook combinations:

```bash
uv run lfx-v2-mockdata --help
uv run lfx-v2-mockdata \
    --jwt-rsa-secret "$JWT_RSA_SECRET" \
    -t playbooks/projects/base_projects
```

**Important Notes:**

- **Order matters!** Playbook directories run in the order specified on the command line.
- Within each directory, playbooks execute in alphabetical order.
- Dependencies between playbooks should be considered when organizing execution order. Multiple passes are made to allow `!ref` calls to be resolved, but the right order will improve performance and help avoid max-retry errors.
- The `!jwt` macro will attempt to detect the JWKS key ID from the endpoint at `http://lfx-platform-heimdall.lfx.svc.cluster.local:4457/.well-known/jwks`. If this URL is not accessible from the execution environment, you must pass an explicit JWT key ID using the `--jwt-key-id` argument.

### Wiping Existing Data

To start fresh, use the reset command:

```bash
make reset
```

This will:

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

### Running After Data Wipe

After using `make reset`, the ROOT project is automatically recreated by the project service pod restart. You can load mock data normally:

```bash
make load
```

**Note:** If you wiped data manually (without the reset command), you'll need to delete the project service pod to trigger ROOT project recreation:

```bash
kubectl delete pod -n lfx $(kubectl get pods -n lfx --no-headers | grep project-service | awk '{print $1}')
```

## Playbook Structure

The playbooks are organized by service type, to allow only loading data for the services you have in your environment.

Please refer to the comments in the YAML files for more information on each playbook's role and purpose.
