// Copyright The Linux Foundation and each contributor to LFX.
// SPDX-License-Identifier: MIT
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"text/template"

	"golang.org/x/text/cases"
	"golang.org/x/text/language"

	"github.com/Masterminds/sprig/v3"
	"github.com/goccy/go-yaml"
	"github.com/goccy/go-yaml/ast"
	"github.com/goccy/go-yaml/parser"
	"github.com/lucasjones/reggen"
	"github.com/ohler55/ojg/jp"
)

type mockDataGenerator struct {
	templates     []string
	yamlIndexFile string
	retries       int
	dump          bool
	dumpJSON      bool
	dryRun        bool
	upload        bool
	force         bool
	config        *Config
	context       interface{}
	httpClient    *http.Client
}

type PlaybookType string

const (
	PlaybookTypeRequest PlaybookType = "request"
)

type RequestParams struct {
	URL     string            `yaml:"url" json:"url"`
	Method  string            `yaml:"method" json:"method"`
	Headers map[string]string `yaml:"headers,omitempty" json:"headers,omitempty"`
	Params  map[string]string `yaml:"params,omitempty" json:"params,omitempty"`
}

type Playbook struct {
	Type   PlaybookType   `yaml:"type" json:"type"`
	Params *RequestParams `yaml:"params,omitempty" json:"params,omitempty"`
	Steps  []interface{}  `yaml:"steps" json:"steps"`
}

type Config struct {
	Playbooks map[string]*Playbook `yaml:"playbooks" json:"playbooks"`
}

type JMESPathRef struct {
	Expression string
	context    interface{}
}

func (j *JMESPathRef) UnmarshalYAML(unmarshal func(interface{}) error) error {
	var value string
	if err := unmarshal(&value); err != nil {
		return err
	}
	j.Expression = value
	return nil
}

func (j *JMESPathRef) MarshalYAML() (interface{}, error) {
	// Evaluate the expression and return the result
	result := j.Evaluate()
	return result, nil
}

func (j *JMESPathRef) MarshalJSON() ([]byte, error) {
	result := j.Evaluate()
	return json.Marshal(result)
}

func (j *JMESPathRef) Evaluate() interface{} {
	if j.context == nil {
		log.Printf("Warning: No context set for JMESPath expression: %s", j.Expression)
		return nil
	}

	expr, err := jp.ParseString(j.Expression)
	if err != nil {
		log.Printf("Error parsing JMESPath expression '%s': %v", j.Expression, err)
		return nil
	}

	results := expr.Get(j.context)
	if len(results) == 0 {
		log.Printf("JMESPath expression '%s' returned no results", j.Expression)
		return nil
	}

	if len(results) == 1 {
		return results[0]
	}
	return results
}
func (m *mockDataGenerator) run() error {
	config, err := m.loadAndPreprocessYAML()
	if err != nil {
		return fmt.Errorf("failed to load and preprocess YAML: %w", err)
	}
	m.config = config
	m.context = config

	if m.dump {
		m.setJMESPathContext(config)
		yamlBytes, err := yaml.Marshal(config)
		if err != nil {
			return fmt.Errorf("failed to marshal config to YAML: %w", err)
		}
		fmt.Print(string(yamlBytes))
	}

	if m.dumpJSON {
		m.setJMESPathContext(config)
		jsonBytes, err := json.MarshalIndent(config, "", "  ")
		if err != nil {
			return fmt.Errorf("failed to marshal config to JSON: %w", err)
		}
		fmt.Println(string(jsonBytes))
	}

	if (m.dump || m.dumpJSON) && !m.upload {
		return nil
	}

	if !m.dryRun {
		if err := m.runPlaybooks(); err != nil {
			return fmt.Errorf("failed to run playbooks: %w", err)
		}
	}

	return nil
}

func (m *mockDataGenerator) extractRefTags(node ast.Node, path string) map[string]string {
	refMap := make(map[string]string)
	m.extractRefTagsRecursive(node, path, refMap)
	return refMap
}

func (m *mockDataGenerator) extractRefTagsRecursive(node ast.Node, path string, refMap map[string]string) {
	if node == nil {
		return
	}

	switch n := node.(type) {
	case *ast.MappingValueNode:
		// This is a key-value pair, process both
		m.extractRefTagsRecursive(n.Key, path, refMap)
		m.extractRefTagsRecursive(n.Value, path, refMap)
	case *ast.MappingNode:
		for _, v := range n.Values {
			var key string
			if keyNode, ok := v.Key.(*ast.StringNode); ok {
				key = keyNode.Value
			}
			newPath := path
			if path != "" {
				newPath = path + "." + key
			} else {
				newPath = key
			}

			// Check if value is a tag node with !ref
			if tagNode, ok := v.Value.(*ast.TagNode); ok {
				if tagNode.Start != nil && tagNode.Start.Value == "!ref" {
					if strNode, ok := tagNode.Value.(*ast.StringNode); ok {
						refMap[newPath] = strNode.Value
					}
				}
			} else {
				m.extractRefTagsRecursive(v.Value, newPath, refMap)
			}
		}
	case *ast.SequenceNode:
		for i, v := range n.Values {
			newPath := fmt.Sprintf("%s[%d]", path, i)

			// Check if value is a tag node with !ref
			if tagNode, ok := v.(*ast.TagNode); ok {
				if tagNode.Start != nil && tagNode.Start.Value == "!ref" {
					if strNode, ok := tagNode.Value.(*ast.StringNode); ok {
						refMap[newPath] = strNode.Value
					}
				}
			} else {
				m.extractRefTagsRecursive(v, newPath, refMap)
			}
		}
	}
}

func (m *mockDataGenerator) applyRefTags(data interface{}, refMap map[string]string) {
	m.applyRefTagsRecursive(data, "", refMap)
}

func (m *mockDataGenerator) applyRefTagsRecursive(data interface{}, path string, refMap map[string]string) {
	switch v := data.(type) {
	case map[string]interface{}:
		for key, val := range v {
			newPath := path
			if path != "" {
				newPath = path + "." + key
			} else {
				newPath = key
			}

			if expr, found := refMap[newPath]; found {
				v[key] = &JMESPathRef{Expression: expr}
			} else {
				m.applyRefTagsRecursive(val, newPath, refMap)
			}
		}
	case []interface{}:
		for i, val := range v {
			newPath := fmt.Sprintf("%s[%d]", path, i)

			if expr, found := refMap[newPath]; found {
				v[i] = &JMESPathRef{Expression: expr}
			} else {
				m.applyRefTagsRecursive(val, newPath, refMap)
			}
		}
	}
}

func (m *mockDataGenerator) loadAndPreprocessYAML() (*Config, error) {
	config := &Config{
		Playbooks: make(map[string]*Playbook),
	}

	for _, templateDir := range m.templates {
		indexPath := filepath.Join(templateDir, m.yamlIndexFile)
		newConfig, err := m.loadYAMLFile(indexPath, templateDir)
		if err != nil {
			return nil, fmt.Errorf("failed to load %s: %w", indexPath, err)
		}

		if err := m.mergeConfigs(config, newConfig); err != nil {
			return nil, fmt.Errorf("failed to merge configs: %w", err)
		}
	}

	return config, nil
}

func (m *mockDataGenerator) loadYAMLFile(path string, baseDir string) (*Config, error) {
	tmplData, err := m.processTemplate(path, baseDir)
	if err != nil {
		return nil, fmt.Errorf("failed to process template %s: %w", path, err)
	}

	// Parse YAML to AST to preserve tag information
	file, err := parser.ParseBytes(tmplData, 0)
	if err != nil {
		return nil, fmt.Errorf("failed to decode YAML: %w", err)
	}

	// Convert to interface{} first to process refs
	var rawData interface{}
	if err := yaml.NodeToValue(file.Docs[0].Body, &rawData); err != nil {
		return nil, fmt.Errorf("failed to convert to interface{}: %w", err)
	}

	// Extract !ref tags from AST and apply them to the decoded data
	// Start with "playbooks" as the base path since that's the root key in our YAML structure
	refMap := m.extractRefTags(file.Docs[0].Body, "playbooks")
	m.applyRefTags(rawData, refMap)

	// Now marshal and unmarshal to get proper Config structure
	yamlBytes, err := yaml.Marshal(rawData)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal: %w", err)
	}

	var config Config
	if err := yaml.Unmarshal(yamlBytes, &config); err != nil {
		return nil, fmt.Errorf("failed to unmarshal config: %w", err)
	}

	return &config, nil
}

func (m *mockDataGenerator) processTemplate(path string, baseDir string) ([]byte, error) {
	content, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("failed to read file: %w", err)
	}
	if content == nil {
		return nil, fmt.Errorf("failed to read file: %s", path)
	}

	funcMap := template.FuncMap{
		"environ":       func() map[string]string { return getEnvMap() },
		"generate_name": generateName,
		"lorem":         loremFunc,
	}

	for k, v := range sprig.FuncMap() {
		funcMap[k] = v
	}

	tmpl, err := template.New(filepath.Base(path)).Funcs(funcMap).Parse(string(content))
	if err != nil {
		return nil, fmt.Errorf("failed to parse template: %w", err)
	}

	processIncludes := func(input string) string {
		includeTag := "!include "
		lines := strings.Split(input, "\n")
		for i, line := range lines {
			if strings.Contains(line, includeTag) {
				idx := strings.Index(line, includeTag)
				if idx >= 0 {
					includePath := strings.TrimSpace(line[idx+len(includeTag):])
					fullPath := filepath.Join(baseDir, includePath)

					includeContent, err := m.processTemplate(fullPath, baseDir)
					if err != nil {
						log.Printf("Error processing include %s: %v", includePath, err)
						continue
					}

					// Convert raw YAML bytes to string and inline directly
					// This preserves custom YAML tags like !ref
					yamlContent := string(includeContent)
					// Strip the leading "---" document marker if present
					yamlContent = strings.TrimPrefix(yamlContent, "---\n")
					yamlContent = strings.TrimPrefix(yamlContent, "---\r\n")
					yamlContent = strings.TrimSpace(yamlContent)

					// Indent the content to match the current indentation level
					indented := strings.ReplaceAll(yamlContent, "\n", "\n"+strings.Repeat(" ", idx))
					lines[i] = line[:idx] + indented
				}
			}
		}
		return strings.Join(lines, "\n")
	}

	var buf bytes.Buffer
	if err := tmpl.Execute(&buf, nil); err != nil {
		return nil, fmt.Errorf("failed to execute template: %w", err)
	}

	result := processIncludes(buf.String())
	return []byte(result), nil
}

func (m *mockDataGenerator) mergeConfigs(dst, src *Config) error {
	for name, playbook := range src.Playbooks {
		if _, exists := dst.Playbooks[name]; exists {
			log.Printf("Warning: playbook %s already exists, skipping", name)
			continue
		}
		dst.Playbooks[name] = playbook
	}
	return nil
}

func (m *mockDataGenerator) setJMESPathContext(context interface{}) {
	var setContext func(v interface{})
	setContext = func(v interface{}) {
		switch val := v.(type) {
		case *JMESPathRef:
			val.context = context
		case map[string]interface{}:
			for _, v := range val {
				setContext(v)
			}
		case []interface{}:
			for _, v := range val {
				setContext(v)
			}
		case *Playbook:
			if val.Steps != nil {
				setContext(val.Steps)
			}
		case map[string]*Playbook:
			for _, p := range val {
				setContext(p)
			}
		}
	}
	setContext(m.config)
}

func (m *mockDataGenerator) runPlaybooks() error {
	for retriesRemaining := m.retries; retriesRemaining >= 0; retriesRemaining-- {
		for name, playbook := range m.config.Playbooks {
			if playbook.Type == "" {
				if m.force {
					log.Printf("Playbook %s missing type, skipping", name)
					continue
				}
				return fmt.Errorf("playbook %s missing type", name)
			}

			if playbook.Type == PlaybookTypeRequest {
				if err := m.runRequestPlaybook(name, playbook, retriesRemaining); err != nil {
					if m.force {
						log.Printf("Error running playbook %s: %v", name, err)
						continue
					}
					return err
				}
			} else {
				if m.force {
					log.Printf("Playbook %s has unknown type %s, skipping", name, playbook.Type)
					continue
				}
				return fmt.Errorf("playbook %s has unknown type %s", name, playbook.Type)
			}
		}
	}
	return nil
}

func (m *mockDataGenerator) runRequestPlaybook(name string, playbook *Playbook, retriesRemaining int) error {
	if playbook.Params == nil {
		if m.force {
			log.Printf("Playbook %s missing params, skipping", name)
			return nil
		}
		return fmt.Errorf("playbook %s missing params", name)
	}

	if playbook.Steps == nil || len(playbook.Steps) == 0 {
		if m.force {
			log.Printf("Playbook %s missing steps, skipping", name)
			return nil
		}
		return fmt.Errorf("playbook %s missing steps", name)
	}

	for i, step := range playbook.Steps {
		stepMap, ok := step.(map[string]interface{})
		if !ok {
			continue
		}

		if _, hasResponse := stepMap["_response"]; hasResponse {
			continue
		}

		m.setJMESPathContext(m.config)

		var body []byte
		var err error

		if playbook.Params.Method == "POST" || playbook.Params.Method == "PUT" || playbook.Params.Method == "PATCH" {
			body, err = json.Marshal(step)
			if err != nil {
				if m.dryRun {
					if m.force {
						log.Printf("Error marshaling step %d in playbook %s: %v", i, name, err)
						stepMap["_response"] = map[string]interface{}{}
						continue
					}
					return fmt.Errorf("error marshaling step %d: %w", i, err)
				}

				if retriesRemaining > 0 {
					continue
				}

				if m.force {
					log.Printf("Error marshaling step %d in playbook %s: %v", i, name, err)
					continue
				}
				return fmt.Errorf("error marshaling step %d: %w", i, err)
			}
		}

		if m.dryRun {
			return nil
		}

		log.Printf("Running step %d for playbook %s: %s %s", i, name, playbook.Params.Method, playbook.Params.URL)

		req, err := http.NewRequest(playbook.Params.Method, playbook.Params.URL, bytes.NewReader(body))
		if err != nil {
			if m.force {
				log.Printf("Error creating request for step %d in playbook %s: %v", i, name, err)
				continue
			}
			return fmt.Errorf("error creating request: %w", err)
		}

		for k, v := range playbook.Params.Headers {
			req.Header.Set(k, v)
		}

		if body != nil {
			req.Header.Set("Content-Type", "application/json")
		}

		resp, err := m.httpClient.Do(req)
		if err != nil {
			if m.force {
				log.Printf("Request failed for step %d in playbook %s: %v", i, name, err)
				continue
			}
			return fmt.Errorf("request failed: %w", err)
		}
		defer resp.Body.Close()

		respBody, err := io.ReadAll(resp.Body)
		if err != nil {
			if m.force {
				log.Printf("Error reading response for step %d in playbook %s: %v", i, name, err)
				stepMap["_response"] = map[string]interface{}{}
				continue
			}
			return fmt.Errorf("error reading response: %w", err)
		}

		if resp.StatusCode >= 400 {
			if m.force {
				log.Printf("Request failed with status %d for step %d in playbook %s: %s",
					resp.StatusCode, i, name, string(respBody))
				continue
			}
			return fmt.Errorf("request failed with status %d: %s", resp.StatusCode, string(respBody))
		}

		var respData interface{}
		if err := json.Unmarshal(respBody, &respData); err != nil {
			if m.force {
				log.Printf("Error parsing response JSON for step %d in playbook %s: %v", i, name, err)
				stepMap["_response"] = map[string]interface{}{}
				continue
			}
			return fmt.Errorf("error parsing response JSON: %w", err)
		}

		stepMap["_response"] = respData
	}

	return nil
}

func getEnvMap() map[string]string {
	env := make(map[string]string)
	for _, e := range os.Environ() {
		pair := strings.SplitN(e, "=", 2)
		if len(pair) == 2 {
			env[pair[0]] = pair[1]
		}
	}
	return env
}

func generateName(args ...string) string {
	style := "lowercase"
	if len(args) > 0 {
		if args[0] == "capital" {
			style = "capital"
		}
	}

	gen, _ := reggen.NewGenerator("[a-z]{5,10}")
	name := gen.Generate(1)

	if style == "capital" {
		caser := cases.Title(language.English)
		return caser.String(name)
	}
	return name
}

func loremFunc() string {
	gen, _ := reggen.NewGenerator("[A-Z][a-z]{3,8}( [a-z]{3,8}){2,5}\\.")
	return gen.Generate(1)
}
