# LFX V2 Mock Data Generator

A Go implementation of the mock data generator for LFX V2 projects. This
tool processes YAML templates with Go's text/template syntax and uploads
mock data to specified API endpoints.

## Features

- **Template Processing**: Uses Go's text/template syntax with Sprig functions for powerful templating
- **YAML with References**: Supports `!ref` tags for JMESPath expressions and `!include` tags for file inclusion
- **HTTP Requests**: Uploads mock data via configurable HTTP requests
- **Environment Variables**: Configurable via environment variables or `.env` file
- **Dry Run Mode**: Preview operations without making actual API calls
- **Retry Logic**: Automatic retry for resolving dependencies and handling transient errors

## Installation

### Prerequisites

- Go 1.24 or later
- Make (optional, for using Makefile commands)

### Building from Source

```bash
# Clone the repository
git clone https://github.com/linuxfoundation/lfx-v2-mockdata.git
cd lfx-v2-mockdata

# Install dependencies
go mod download
go mod tidy

# Build the binary
go build -o mockdata main.go generator.go
# or using make
make build
```

## Usage

### Basic Usage

```bash
# Run with template directory
./mockdata -t templates

# Run with multiple template directories
./mockdata -t templates -t additional-templates

# Specify custom index file
./mockdata -t templates --yaml-index-file custom-index.yaml
```

### Command-Line Options

```
-t, --templates        Path to template directory (required, can be specified multiple times)
--yaml-index-file      Index template file name (default: "index.yaml")
--retries             Number of retries for dependencies/errors (default: 10)
--dump                Dump parsed YAML to stdout (no !ref expansion)
--dump-json           Dump parsed JSON to stdout (with !ref expansion)
--dry-run             Do not upload any data to endpoints
--upload              Upload to endpoints even when dumping
--force               Keep running steps after a failure
```

### Examples

```bash
# Preview parsed templates as YAML
./mockdata -t templates --dump

# Preview with reference expansion as JSON
./mockdata -t templates --dump-json

# Dry run without making API calls
./mockdata -t templates --dry-run

# Force continuation on errors
./mockdata -t templates --force

# Upload with custom retry count
./mockdata -t templates --retries 5
```

## Writing Mock Data Templates

### Directory Structure

```
templates/
├── index.yaml           # Main entry point
├── root.yaml           # Base project definitions
├── global_groups.yaml  # Permission group definitions
├── extra_incorporated.yaml  # Additional projects
└── n_depth.yaml        # Nested project hierarchies
```

### Template Syntax

Templates use Go's text/template syntax with additional helpers:

#### Available Template Functions

- **`environ`**: Returns environment variables as a map
- **`generate_name`**: Generates random names (accepts "capital" or "lowercase")
- **`lorem`**: Generates lorem ipsum sentences
- **Sprig functions**: All [Sprig](https://masterminds.github.io/sprig/) template functions are available

#### Example Template

```yaml
---
type: request
params:
  url: {{ environ.PROJECTS_URL | default "http://localhost:8080/projects" }}
  method: POST
steps:
  - slug: my-project
    name: {{ generate_name "capital" }} Foundation
    description: >-
      {{ lorem }}
    public: true
```

### YAML Tags

#### `!include` Tag

Include another YAML file:

```yaml
playbooks:
  base: !include base.yaml
  extra: !include extra.yaml
```

#### `!ref` Tag

Reference values using JMESPath expressions:

```yaml
steps:
  - name: child-project
    parent_uid: !ref playbooks.base.steps[?slug == 'parent']._response.uid | [0]
```

### Playbook Structure

Each playbook must have:
- `type`: Currently only "request" is supported
- `params`: Request parameters (url, method, headers, params)
- `steps`: Array of payloads to send

Example:

```yaml
type: request
params:
  url: http://api.example.com/projects
  method: POST
  headers:
    Authorization: Bearer token
steps:
  - name: Project 1
    description: First project
  - name: Project 2
    description: Second project
```

### Template Variables and Loops

Use Go template syntax for loops and conditions:

```yaml
steps:
  {{ range $i := seq 0 9 }}
  - slug: project_{{ $i }}
    name: Project {{ $i }}
    description: >-
      This is project number {{ $i }}
  {{ end }}
```

### Response Handling

Responses from API calls are stored in the `_response` field and can be referenced:

```yaml
steps:
  - slug: parent-project
    name: Parent Project
  
  - slug: child-project
    name: Child Project
    parent_uid: !ref playbooks.current.steps[0]._response.uid
```

## Configuration

### Environment Variables

Create a `.env` file in the project root:

```env
# API Endpoints
PROJECTS_URL=http://localhost:8080/projects
LFX_API_KEY=mock-api-key
FGA_API_URL=http://localhost:8081
FGA_STORE_ID=mock-store-id
```

### Using Environment Variables in Templates

```yaml
params:
  url: {{ environ.PROJECTS_URL | default "http://localhost:8080/projects" }}
  headers:
    Authorization: Bearer {{ environ.LFX_API_KEY }}
```

## Development

### Running Tests

```bash
go test -v ./...
# or
make test
```

### Using the Makefile

```bash
make build      # Build the binary
make deps       # Download dependencies
make run        # Run with example templates
make dump       # Dump parsed YAML
make dump-json  # Dump parsed JSON
make dry-run    # Run without uploading
make clean      # Clean build artifacts
make help       # Show help
```

### Local Development Setup

For local testing, this project includes mock servers that simulate the LFX API endpoints:

#### Quick Start

```bash
# 1. Copy environment template
cp .env.example .env

# 2. Start mock servers and generate data
make generate

# 3. Verify mock servers are running
make mock-health

# 4. Check generated projects
make mock-projects
```

#### Mock Server Management

```bash
# Start mock servers
make docker-up

# Stop mock servers
make docker-down

# View logs
make docker-logs                  # All logs
make docker-logs-projects         # Projects API logs only
make docker-logs-fga              # FGA API logs only

# Rebuild mock servers
make docker-rebuild
```

#### Mock Server Endpoints

- **Projects API**: `http://localhost:8080` (requires `LFX_API_KEY` header)
- **FGA API**: `http://localhost:8081` (no authentication required)

Both servers provide health check endpoints at `/health`.

For more details on the mock server implementation, see `MOCKSERVER.md`.

## Advanced Features

### Retry Logic

The tool automatically retries operations to handle:
- Unresolved `!ref` dependencies
- Transient HTTP errors
- Network timeouts

Configure with `--retries` flag (default: 10).

### Error Handling

- Use `--force` to continue execution after errors
- Failed steps store empty `_response` objects to prevent re-execution
- Detailed error logging for debugging

### Parallel Template Processing

Multiple template directories are processed and merged:

```bash
./mockdata -t base-templates -t overlay-templates -t custom-templates
```

Templates are merged in order, with later templates taking precedence for conflicting playbook names.

## Troubleshooting

### Common Issues

1. **JMESPath reference not found**
   - Ensure the referenced path exists
   - Check that previous steps have completed successfully
   - Verify the expression syntax

2. **Template parsing errors**
   - Validate Go template syntax
   - Check for unclosed brackets or quotes
   - Ensure all variables are defined

3. **HTTP request failures**
   - Verify endpoint URLs in environment variables
   - Check network connectivity
   - Ensure API authentication is configured

4. **Include file not found**
   - Use relative paths from the template directory
   - Ensure included files exist
   - Check file permissions

### Debug Mode

For detailed logging, run with verbose output:

```bash
# See what would be uploaded without making requests
./mockdata -t templates --dry-run --dump-json

# Force execution to see all errors
./mockdata -t templates --force
```

## Migration from Python

This Go implementation maintains compatibility with the Python version's template format while offering:

- **Better Performance**: Compiled binary with concurrent execution
- **Simplified Deployment**: Single binary with no Python dependencies
- **Type Safety**: Go's type system catches errors at compile time
- **Cross-Platform**: Easy compilation for different OS/architectures

### Differences from Python Version

1. **Template Syntax**: Uses Go's text/template instead of Jinja2
   - `{% for %}` becomes `{{ range }}`
   - `{{ var | filter }}` uses Go template pipeline syntax
   - Sprig functions replace some Jinja2 filters

2. **Name Generation**: Simplified random name generation
   - Uses regex-based generation instead of names-generator library
   - Supports "capital" and "lowercase" styles

3. **Lorem Ipsum**: Basic lorem generation
   - Simplified sentence generation
   - Can be extended with additional lorem functions

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Run tests and ensure they pass
6. Submit a pull request

## License

This project is licensed under the MIT License. See LICENSE file for details.

## Support

For issues, questions, or contributions, please visit the [GitHub repository](https://github.com/linuxfoundation/lfx-v2-mockdata).
