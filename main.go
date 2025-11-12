// Copyright The Linux Foundation and each contributor to LFX.
// SPDX-License-Identifier: MIT
package main

import (
	"flag"
	"log"
	"net/http"
	"strings"
	"time"

	"github.com/joho/godotenv"
)

type stringSlice []string

func (s *stringSlice) String() string {
	return strings.Join(*s, ", ")
}

func (s *stringSlice) Set(value string) error {
	*s = append(*s, value)
	return nil
}

func main() {
	var (
		templates     stringSlice
		yamlIndexFile = flag.String("yaml-index-file", "index.yaml", "index or main template file name")
		retries       = flag.Int("retries", 10, "number of retries to resolve !ref dependencies or HTTP errors")
		dump          = flag.Bool("dump", false, "dump the parsed templates as YAML to stdout (no !ref expansion)")
		dumpJSON      = flag.Bool("dump-json", false, "dump the parsed templates as JSON to stdout (with !ref expansion)")
		dryRun        = flag.Bool("dry-run", false, "do not upload any data to endpoints")
		upload        = flag.Bool("upload", false, "upload to endpoints even when dumping")
		force         = flag.Bool("force", false, "keep running steps after a failure")
	)

	flag.Var(&templates, "templates", "path to collections template directory (can be specified multiple times)")
	flag.Var(&templates, "t", "path to collections template directory (shorthand)")
	flag.Parse()

	if len(templates) == 0 {
		log.Fatal("At least one template directory must be specified with -t or --templates")
	}

	if err := godotenv.Load(); err != nil {
		log.Printf("No .env file loaded: %v", err)
	}

	gen := &mockDataGenerator{
		templates:     templates,
		yamlIndexFile: *yamlIndexFile,
		retries:       *retries,
		dump:          *dump,
		dumpJSON:      *dumpJSON,
		dryRun:        *dryRun,
		upload:        *upload,
		force:         *force,
		httpClient:    &http.Client{Timeout: 30 * time.Second},
	}

	if err := gen.run(); err != nil {
		log.Fatal(err)
	}
}
