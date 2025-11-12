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

### 1. Set Environment Variables

#### NATS Configuration

```bash
export NATS_URL="lfx-platform-nats.lfx.svc.cluster.local:4222"
```

#### OpenFGA Configuration

First, confirm the OpenFGA Store ID:

```bash
curl -sSi "http://lfx-platform-openfga.lfx.svc.cluster.local:8080/stores"
```

Then export the Store ID:

```bash
export OPENFGA_STORE_ID="your-store-id-here"
```

#### Authentication Tokens

Generate impersonated Heimdall JWTs for service calls using the provided helper script:

```bash
PROJECTS_TOKEN="$(./scripts/mock-heimdall-jwt.sh lfx-v2-project-service "clients@m2m_helper")"
COMMITTEES_TOKEN="$(./scripts/mock-heimdall-jwt.sh lfx-v2-committee-service "clients@m2m_helper")"
export PROJECTS_TOKEN COMMITTEES_TOKEN
```

*Note: in the future we may replace this with a YAML `!jwt` macro, and pass in the just the signing key as an environment variable.*

## Usage

### Running Mock Data Generation

Use uv to run the mock data tool (uv will automatically manage Python versions and virtual environments):

```bash
# Test the script (uv will create the virtual environment automatically).
uv run lfx-v2-mockdata --help
# Load some data!
uv run lfx-v2-mockdata -t playbooks/projects/{root_project_access,base_projects,extra_projects} playbooks/committees/base_committees
```

**Important Notes:**
- **Order matters!** Playbook directories run in the order specified on the command line.
- Within each directory, playbooks execute in alphabetical order.
- Dependencies between playbooks should be considered when organizing execution order. Multiple passes are made to allow `!ref` calls to be resolved, but the right order will improve performance and help avoid max-retry errors.

### Wiping Existing Data

If you need to start fresh, wipe the NATS KV buckets:

```bash
for bucket in projects project-settings committees committee-settings committee-members; do
    nats kv rm -f $bucket
    nats kv add $bucket
done
```

*Consider updating this documentation to also provide steps for recreating the OpenSearch index. Stale OpenFGA tuples may also be deleted, but unlike OpenSearch data, it won't impact the refreshed data to keep them.*

### Running After Data Wipe

When running after wiping data, you need to recreate the ROOT project first:

```bash
uv run lfx-v2-mockdata -t playbooks/projects/recreate_root_project playbooks/projects/{root_project_access,base_projects,extra_projects} playbooks/committees/base_committees
```

The `recreate_root_project` playbook bypasses the API and directly creates a new ROOT project in the NATS KV bucket.

## Playbook Structure

The playbooks are organized by service type, to allow only loading data for the services you have in your environment.

Please refer to the comments in the YAML files for more information on each playbook's role and purpose.
