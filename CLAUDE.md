# CLAUDE.md - LFX V2 Mock Data Generator

This document provides context for AI assistants working with this codebase.

## Project Overview

This is a Go-based mock data generator for LFX V2 projects. It processes
YAML templates with Go template syntax and uploads generated mock data
to API endpoints. The tool supports advanced features like JMESPath
references, file inclusion, and retry logic for handling dependencies.

**Primary Purpose**: Generate and upload hierarchical mock data for
testing LFX V2 project APIs and Fine-Grained Authorization (FGA)
systems.

## Key Concepts

### 1. Template Processing
- Templates use Go's `text/template` syntax (not Jinja2)
- Supports Sprig template functions for enhanced functionality
- Custom functions: `environ`, `generate_name`, `lorem`
- Templates are processed before YAML parsing to allow dynamic generation

### 2. YAML Custom Tags
- **`!ref` tag**: Creates JMESPath references to other data in the config
  - Example: `!ref playbooks.base.steps[0]._response.uid`
  - References are resolved during JSON marshaling
  - Requires retry logic as dependencies may not be available initially
- **`!include` tag**: Includes external YAML files inline
  - Preserves custom tags from included files
  - Uses relative paths from the template directory

### 3. Playbooks
- Main execution unit containing request configuration and steps
- Each playbook defines:
  - `type`: Currently only "request" supported
  - `params`: HTTP request configuration (URL, method, headers)
  - `steps`: Array of payloads to send as POST/PUT requests
- Steps accumulate `_response` fields after execution for later reference

## Architecture

### File Structure

```
.
├── main.go              # CLI entry point, flag parsing
├── generator.go         # Core logic for template processing and execution
├── generator_test.go    # Unit tests
├── Makefile             # Build and development commands
├── docker-compose.yml   # Local mock server setup
├── .env.example         # Environment variable template
├── templates/           # Example YAML templates
│   ├── index.yaml       # Main entry point with !include directives
│   ├── root.yaml        # Base project definitions
│   ├── global_groups.yaml
│   ├── extra_incorporated.yaml
│   └── n_depth.yaml     # Nested project hierarchies
└── mockserver/          # Local mock API server for testing
```

### Core Components

#### `mockDataGenerator` (generator.go:23-35)
Main struct orchestrating the entire process:
- Loads and preprocesses YAML templates
- Manages JMESPath reference resolution
- Executes HTTP requests for playbooks
- Handles retry logic

#### Key Methods

1. **`run()`** (generator.go:108): Main execution flow
   - Loads/preprocesses YAML  Dumps output  Runs playbooks

2. **`loadAndPreprocessYAML()`** (generator.go:239): Template loading
   - Processes multiple template directories
   - Merges configs from all sources
   - Preserves custom YAML tags

3. **`processTemplate()`** (generator.go:296): Template rendering
   - Executes Go templates with custom functions
   - Processes `!include` directives inline
   - Returns processed YAML bytes

4. **`extractRefTags()` / `applyRefTags()`** (generator.go:147, 205): Reference handling
   - Extracts `!ref` tags from AST before YAML decoding
   - Applies them as `JMESPathRef` objects in decoded data
   - Critical for maintaining reference semantics through marshaling

5. **`runPlaybooks()`** (generator.go:400): Execution engine
   - Iterates with retry logic (default: 10 retries)
   - Skips steps with existing `_response` fields
   - Stores responses for reference resolution

#### `JMESPathRef` (generator.go:60-107)
Custom type for lazy evaluation of JMESPath expressions:
- Unmarshals as string during YAML parsing
- Evaluates expression during JSON marshaling
- Requires context to be set via `setJMESPathContext()`

## Development Workflows

### Common Commands

```bash
# Build
make build              # Compile binary to ./mockdata
go build -o mockdata main.go generator.go

# Run
make run                # Run with templates/ directory
make generate           # Start mock servers + generate data
./mockdata -t templates # Direct execution

# Testing
make test               # Run all tests
go test -v              # Verbose test output
go test -v -run TestExtractRefTags  # Run specific test

# Debug/Inspect
make dump               # See parsed YAML (no ref expansion)
make dump-json          # See final JSON (refs expanded)
make dry-run            # Parse but don't send HTTP requests

# Mock Server Management
make docker-up          # Start local mock APIs
make docker-logs        # View all logs
make mock-health        # Check server health
make mock-projects      # List projects in mock API
```

### Testing Strategy

Tests focus on:
1. **Template processing**: Verify Go templates execute correctly
2. **YAML tag handling**: Test `!ref` and `!include` tag extraction/application
3. **Reference resolution**: Ensure JMESPath expressions evaluate properly
4. **Integration**: End-to-end playbook execution with mock servers

Key test file: `generator_test.go`

### Adding New Features

When adding functionality:

1. **New template functions**: Add to `funcMap` in `processTemplate()` (generator.go:305)
2. **New playbook types**: Extend `PlaybookType` enum and add handler in `runPlaybooks()`
3. **New YAML tags**: Implement extraction in `extractRefTagsRecursive()` and application in `applyRefTagsRecursive()`

## Important Patterns and Conventions

### 1. Error Handling
- Use `--force` flag to continue after errors (useful for debugging)
- Failed steps store empty `_response` to prevent re-execution
- Detailed logging via `log.Printf()` for debugging

### 2. Retry Logic
- Outer loop in `runPlaybooks()` retries entire playbook set
- Necessary because `!ref` dependencies may not resolve on first pass
- Steps with responses are skipped on subsequent iterations
- Default: 10 retries (configurable via `--retries`)

### 3. Context Setting for References
- `setJMESPathContext()` must be called before JSON marshaling
- Recursively sets context on all `JMESPathRef` objects
- Context is the entire config, allowing cross-playbook references

### 4. Template vs. YAML Processing Order
1. Load raw file content
2. Execute Go template rendering
3. Process `!include` directives (recursively)
4. Parse YAML to AST
5. Extract custom tags from AST
6. Convert to Go data structures
7. Apply custom tags as special types

### 5. HTTP Client Configuration
- 30-second timeout (main.go:57)
- Responses stored in `_response` field of step
- Content-Type automatically set for POST/PUT/PATCH

## Environment Configuration

Key environment variables (see `.env.example`):

- `PROJECTS_URL`: Projects API endpoint
- `FGA_API_URL`: OpenFGA API endpoint
- `FGA_STORE_ID`: FGA store identifier
- `LFX_API_KEY`: API authentication key

Access in templates: `{{ (environ).PROJECTS_URL }}`

## Common Gotchas

1. **Template Syntax**: Uses Go templates, not Jinja2
   - `{{ range }}...{{ end }}` not `{% for %}...{% endfor %}`
   - Sprig functions replace many Jinja2 filters

2. **Reference Resolution**: May require multiple retries
   - References to responses need previous steps to complete
   - Increase `--retries` if seeing "no results" warnings

3. **Include Paths**: Relative to template directory, not working directory
   - `!include foo.yaml` looks in same dir as including file

4. **Response Field**: `_response` is reserved
   - Automatically added by generator
   - Don't manually add this field to templates

5. **YAML Tag Preservation**: Complex implementation
   - Standard YAML libraries lose custom tags during parsing
   - We extract tags from AST before decoding
   - Tags are reapplied as custom types after decoding

## Dependencies

Key Go modules:
- `github.com/goccy/go-yaml`: YAML parsing with AST support
- `github.com/Masterminds/sprig/v3`: Template functions
- `github.com/ohler55/ojg/jp`: JSONPath/JMESPath evaluation
- `github.com/joho/godotenv`: Environment variable loading
- `github.com/lucasjones/reggen`: Random string generation

## Migration Notes

This is a Go rewrite of a Python implementation:
- Maintains template format compatibility
- Replaces Jinja2 with Go templates
- Better performance and easier deployment
- Type safety and compile-time checking

## Local Development Setup

1. Copy `.env.example` to `.env`
2. Start mock servers: `make docker-up`
3. Build: `make build`
4. Run: `make generate`
5. Verify: `make mock-projects`

## Related Documentation

- `README.md`: User-facing documentation
- `MOCKSERVER.md`: Mock server implementation details
- `templates/`: Example templates showing common patterns
