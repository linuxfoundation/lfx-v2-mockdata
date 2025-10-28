# LFX Mock Server

A mock HTTP server that simulates the LFX Projects API and OpenFGA API endpoints for testing the mockdata generator.

## Features

- **Projects API**: Creates and manages mock projects with unique UIDs
- **OpenFGA API**: Accepts and logs FGA tuple writes
- **Service Modes**: Can run as separate services or combined
- **Health Check**: Provides a health check endpoint
- **Authorization**: Projects API supports Bearer token authentication
- **Configurable**: Host, port, service mode, and API key can be configured via flags or environment variables

## Architecture

The mock server setup runs two separate services:

- **Projects API** (`lfx-projects-api`) - Runs on port 8080
  - Handles `/projects` endpoints
  - Network aliases: `lfx-api.k8s.orb.local`, `lfx-api.lfx.svc.cluster.local`, `projects-api.lfx.svc.cluster.local`
  - **Requires authorization** via Bearer token (except `/health` endpoint)

- **FGA API** (`lfx-fga-api`) - Runs on port 8081
  - Handles `/stores/{store_id}/write` endpoints
  - Network aliases: `openfga.cluster.svc.local`, `openfga.openfga.svc.cluster.local`
  - **No authorization required** (mock server only)

## Service Modes

The server supports three modes via the `--service` flag or `SERVICE_MODE` environment variable:

- `projects` - Only Projects API endpoints (with authorization)
- `fga` - Only OpenFGA API endpoints (no authorization)
- `all` - All endpoints (default)

## Endpoints

### Projects API (mode: `projects` or `all`)
- `POST /projects` - Create a new project (returns a response with a generated `uid`)
- `GET /projects` - List all created projects
- `GET /projects/{slug}` - Get a specific project by slug

**Note:** All Projects API endpoints except `/health` require authorization.

### OpenFGA API (mode: `fga` or `all`)
- `POST /stores/{store_id}/write` - Write FGA tuples

**Note:** FGA API endpoints do not require authorization.

### Common (all modes)
- `GET /health` - Health check endpoint (no authorization required)

## Configuration

The server can be configured using command-line flags or environment variables:

- `--host` or `MOCK_SERVER_HOST`: Host to bind to (default: `0.0.0.0`)
- `--port` or `MOCK_SERVER_PORT`: Port to bind to (default: `8080`)
- `--service` or `SERVICE_MODE`: Service mode - `projects`, `fga`, or `all` (default: `all`)
- `--api-key` or `LFX_API_KEY`: API key for Projects API authorization (default: `mock-api-key`)

## Authorization

### Projects API Authorization

The Projects API requires authorization for all endpoints except `/health`.

Projects API requests must include an `Authorization` header with a Bearer token:

```bash
Authorization: Bearer mock-api-key
```

By default, the Projects API uses `mock-api-key` as the API key. You can customize this:

- **Via environment variable**: Set `LFX_API_KEY` in your environment
- **Via command-line flag**: Use `--api-key your-key` when starting the server
- **Via docker-compose**: Set the `LFX_API_KEY` environment variable in `docker-compose.yml`

Example with custom API key:

```bash
# Start server with custom API key
./mockserver --port 8080 --api-key my-custom-key

# Or via environment variable
export LFX_API_KEY=my-custom-key
./mockserver --port 8080
```

### FGA API Authorization

The FGA API **does not require authorization** in the mock server. This simplifies testing and development. No `Authorization` header is needed for FGA endpoints.

## Quick Start

### 1. Start the mock servers

Using Docker Compose (recommended):

```bash
docker-compose up -d
```

This will start both mock servers:
- Projects API on `http://localhost:8080`
- FGA API on `http://localhost:8081`

Both services include network aliases for the default domain names used in production.

### 2. Configure environment variables

Copy the example environment file and update it:

```bash
cp .env.example .env
```

For local development with the Docker mock servers, use these values in your `.env` file:

```bash
# Projects API endpoint (runs on port 8080)
PROJECTS_URL=http://localhost:8080/projects

# LFX API Key (used for Projects API authorization only)
LFX_API_KEY=mock-api-key

# OpenFGA configuration (runs on port 8081, no authorization required)
FGA_API_URL=http://localhost:8081
FGA_STORE_ID=mock-store-id
```

**Note:** The `LFX_API_KEY` value must match the API key configured on the Projects API mock server. The mockdata generator will automatically include this key in the `Authorization: Bearer` header for Projects API requests. FGA requests do not require authorization headers.

### 3. Run the mockdata generator

```bash
go run . -t templates
```

This will:
1. Read all template files from the `templates/` directory
2. Generate mock data according to the templates
3. Send POST requests to the mock server
4. Store responses and use them in subsequent requests via `!ref` tags

### 4. View the generated data

To see what would be generated without uploading:

```bash
# Dump as YAML
go run . -t templates --dump

# Dump as JSON (with !ref expansion)
go run . -t templates --dump-json
```

## Running with Docker

### Build the image

From the repository root:

```bash
docker build -t lfx-mockserver -f mockserver/Dockerfile mockserver
```

### Run the container

```bash
# Run with default settings
docker run -p 8080:8080 lfx-mockserver

# Run with custom configuration
docker run -p 9000:9000 -e MOCK_SERVER_PORT=9000 -e LFX_API_KEY=custom-key lfx-mockserver

# Run Projects API only
docker run -p 8080:8080 -e SERVICE_MODE=projects lfx-mockserver

# Run FGA API only
docker run -p 8081:8080 -e SERVICE_MODE=fga lfx-mockserver
```

### Using with Docker Network

If you want to run the mockdata generator in a container and have it communicate with the mock server:

#### Build the generator

```bash
docker build -t lfx-mockdata-generator .
```

#### Run on the same network

```bash
docker run --rm --network lfx-network \
  -v $(pwd)/templates:/templates \
  -e PROJECTS_URL=http://lfx-api.k8s.orb.local/projects \
  -e FGA_API_URL=http://openfga.cluster.svc.local:8080 \
  -e FGA_STORE_ID=mock-store-id \
  -e LFX_API_KEY=mock-api-key \
  lfx-mockdata-generator -t /templates
```

The Docker network aliases ensure that the generator can resolve the service names used in the templates.

Available network aliases:
- Projects API: `lfx-api.k8s.orb.local` (default), `lfx-api.lfx.svc.cluster.local`, `projects-api.lfx.svc.cluster.local`
- FGA API: `openfga.cluster.svc.local` (default), `openfga.openfga.svc.cluster.local`

## Running Locally

### Prerequisites

- Go 1.24 or later

### Build and run

```bash
cd mockserver
go mod download
go build -o mockserver .
./mockserver
```

### Run with custom configuration

```bash
# Projects API only
./mockserver --service projects --port 8080

# FGA API only
./mockserver --service fga --port 8081

# All endpoints
./mockserver --service all --port 8080

# Custom host, port, and API key
./mockserver --host 127.0.0.1 --port 9000 --api-key my-secret-key
```

Or using environment variables:

```bash
# Projects API only
SERVICE_MODE=projects MOCK_SERVER_PORT=8080 ./mockserver

# FGA API only
SERVICE_MODE=fga MOCK_SERVER_PORT=8081 ./mockserver

# Custom configuration
MOCK_SERVER_HOST=127.0.0.1 MOCK_SERVER_PORT=9000 SERVICE_MODE=all LFX_API_KEY=my-secret-key ./mockserver
```

## Testing

Test the server with curl:

```bash
# Health check (no authorization required)
curl http://localhost:8080/health

# Create a project (requires authorization)
curl -X POST http://localhost:8080/projects \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer mock-api-key" \ #gitleaks:allow
  -d '{
    "slug": "test-project",
    "name": "Test Project",
    "description": "A test project",
    "public": true
  }'

# List projects (requires authorization)
curl -H "Authorization: Bearer mock-api-key" http://localhost:8080/projects #gitleaks:allow

# Get a specific project (requires authorization)
curl -H "Authorization: Bearer mock-api-key" http://localhost:8080/projects/test-project #gitleaks:allow

# Write FGA tuples (no authorization required)
curl -X POST http://localhost:8081/stores/test-store/write \
  -H "Content-Type: application/json" \
  -d '{
    "writes": {
      "tuple_keys": [
        {
          "user": "team:admins#member",
          "relation": "owner",
          "object": "project:12345"
        }
      ]
    }
  }'
```

## Checking Mock Server Logs

To see what the mock server is receiving:

```bash
docker-compose logs -f
```

To view logs for a specific service:

```bash
docker-compose logs -f projects-api
docker-compose logs -f fga-api
```

You should see output like:

```
lfx-projects-api | 2024/01/01 12:00:00 Mock server starting on 0.0.0.0:8080 (mode: projects)
lfx-projects-api | 2024/01/01 12:00:00 Created project: slug=ROOT, uid=abc123, name=LFX_ROOT
lfx-projects-api | 2024/01/01 12:00:01 Created project: slug=tlf, uid=def456, name=The Linux Foundation
lfx-fga-api      | 2024/01/01 12:00:02 Mock server starting on 0.0.0.0:8080 (mode: fga)
lfx-fga-api      | 2024/01/01 12:00:02 FGA Write: store=mock-store-id, user=team:project_super_admins#member, relation=owner, object=project:abc123
```

## Stopping the Mock Server

```bash
docker-compose down
```

To also remove the network:

```bash
docker-compose down --volumes
```

## Customization

### Changing the ports

Edit `docker-compose.yml`:

```yaml
services:
  projects-api:
    ports:
      - "9000:8080"  # Map host port 9000 to container port 8080
  fga-api:
    ports:
      - "9001:8080"  # Map host port 9001 to container port 8080
```

Then update your `.env` file:

```bash
PROJECTS_URL=http://localhost:9000/projects
FGA_API_URL=http://localhost:9001
```

### Adding more endpoints

Edit `mockserver/server.go` and add new handlers:

```go
r.HandleFunc("/your-endpoint", yourHandler).Methods("POST")
```

Then rebuild:

```bash
docker-compose up -d --build
```

## Troubleshooting

### Generator can't connect to mock servers

1. Check if the mock servers are running:
   ```bash
   curl http://localhost:8080/health  # Projects API
   curl http://localhost:8081/health  # FGA API
   ```

   Note: The health endpoints don't require authorization.

2. Verify Docker containers are running:
   ```bash
   docker-compose ps
   ```

### Authorization errors (401 Unauthorized)

If you're getting 401 errors when making **Projects API** requests:

1. Verify the API key in your `.env` file matches the server's API key:
   ```bash
   # Check what key the server expects (from logs)
   docker-compose logs projects-api | grep "API Key"

   # Verify your .env file
   grep LFX_API_KEY .env
   ```

2. Test authorization manually:
   ```bash
   # This should fail with 401
   curl http://localhost:8080/projects

   # This should succeed
   curl -H "Authorization: Bearer mock-api-key" http://localhost:8080/projects #gitleaks:allow
   ```

3. Check that your Projects API templates include the Authorization header:
   ```yaml
   headers:
     Content-Type: application/json
     Authorization: Bearer {{ environ.LFX_API_KEY | default "unset" }}
   ```

**Note:** FGA API endpoints do not require authorization, so you should not receive 401 errors from FGA endpoints.

### Connection issues

1. Check Docker logs:
   ```bash
   docker-compose logs projects-api
   docker-compose logs fga-api
   ```

2. Verify the network:
   ```bash
   docker network ls | grep lfx
   docker network inspect lfx-network
   ```

### Projects not being created

1. Check that your `.env` file is being loaded
2. Verify the URLs in your templates match the mock server endpoints:
   - Projects: `http://localhost:8080/projects`
   - FGA: `http://localhost:8081/stores/{store_id}/write`
3. Check mock server logs for errors:
   ```bash
   docker-compose logs -f projects-api
   docker-compose logs -f fga-api
   ```

### Permission issues

Make sure Docker has permission to bind to the port:

```bash
# Linux: you may need to use a port > 1024 or run with sudo
sudo docker-compose up -d
```
