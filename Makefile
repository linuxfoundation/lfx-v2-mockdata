# Copyright The Linux Foundation and each contributor to LFX.
# SPDX-License-Identifier: MIT
.PHONY: all build run deps clean test dump dump-json help docker-build docker-up docker-down docker-logs docker-logs-projects docker-logs-fga docker-rebuild mock-health mock-projects generate

# Default target
all: build

# Build the Go binary
build:
	go build -o mockdata main.go generator.go

# Install dependencies
deps:
	go mod download
	go mod tidy

# Run with example templates
run: build
	./mockdata -t templates

# Dump parsed YAML (no ref expansion)
dump: build
	./mockdata -t templates --dump

# Dump as JSON (with ref expansion)
dump-json: build
	./mockdata -t templates --dump-json

# Dry run (no actual uploads)
dry-run: build
	./mockdata -t templates --dry-run

# Run tests
test:
	go test -v ./...

# Clean build artifacts
clean:
	rm -f mockdata
	rm -f mockserver/mockserver

# Docker targets for mock server
docker:
	cd mockserver && docker build -t lfx-mockserver .

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

docker-logs-projects:
	docker compose logs -f projects-api

docker-logs-fga:
	docker compose logs -f fga-api

docker-rebuild:
	docker compose up -d --build

# Check mock server health
mock-health:
	@echo "Projects API:"
	@curl -s http://localhost:8080/health | jq . || echo "Projects API not running"
	@echo "\nFGA API:"
	@curl -s http://localhost:8081/health | jq . || echo "FGA API not running"

# List projects in mock server
mock-projects:
	@curl -s http://localhost:8080/projects | jq . || echo "Projects API not running or jq not installed"

# Generate mock data (starts mock servers if needed)
generate:
	@if ! docker compose ps | grep -q projects-api; then \
		echo "Starting mock servers..."; \
		docker compose up -d; \
		sleep 3; \
	fi
	@if [ ! -f .env ]; then \
		echo "Creating .env file from .env.example..."; \
		cp .env.example .env; \
	fi
	go run main.go generator.go -t templates

# Show help
help:
	@echo "Available targets:"
	@echo ""
	@echo "Build & Run:"
	@echo "  make build        - Build the mockdata binary"
	@echo "  make deps         - Download and tidy dependencies"
	@echo "  make run          - Run with example templates"
	@echo "  make generate     - Start mock server and generate data"
	@echo ""
	@echo "Output & Testing:"
	@echo "  make dump         - Dump parsed YAML to stdout"
	@echo "  make dump-json    - Dump parsed JSON to stdout"
	@echo "  make dry-run      - Run without uploading data"
	@echo "  make test         - Run tests"
	@echo ""
	@echo "Mock Servers:"
	@echo "  make docker            - Build mock server Docker image"
	@echo "  make docker-up         - Start mock servers with docker compose"
	@echo "  make docker-down       - Stop mock servers"
	@echo "  make docker-logs       - Show all mock server logs"
	@echo "  make docker-logs-projects - Show Projects API logs"
	@echo "  make docker-logs-fga   - Show FGA API logs"
	@echo "  make docker-rebuild    - Rebuild and restart mock servers"
	@echo "  make mock-health       - Check mock servers health"
	@echo "  make mock-projects     - List projects in Projects API"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean        - Remove build artifacts"
