// Copyright The Linux Foundation and each contributor to LFX.
// SPDX-License-Identifier: MIT
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"strings"

	"github.com/google/uuid"
	"github.com/gorilla/mux"
)

type ProjectRequest struct {
	Slug        string `json:"slug"`
	Name        string `json:"name"`
	Description string `json:"description,omitempty"`
	Public      bool   `json:"public,omitempty"`
	ParentUID   string `json:"parent_uid,omitempty"`
}

type ProjectResponse struct {
	UID         string `json:"uid"`
	Slug        string `json:"slug"`
	Name        string `json:"name"`
	Description string `json:"description,omitempty"`
	Public      bool   `json:"public,omitempty"`
	ParentUID   string `json:"parent_uid,omitempty"`
}

type FGAWriteRequest struct {
	Writes      *FGAWrites      `json:"writes,omitempty"`
	Deletes     *FGADeletes     `json:"deletes,omitempty"`
	AuthContext *FGAAuthContext `json:"authorization_model_id,omitempty"`
}

type FGAWrites struct {
	TupleKeys []FGATupleKey `json:"tuple_keys"`
}

type FGADeletes struct {
	TupleKeys []FGATupleKey `json:"tuple_keys"`
}

type FGATupleKey struct {
	User     string `json:"user"`
	Relation string `json:"relation"`
	Object   string `json:"object"`
}

type FGAAuthContext struct {
	AuthorizationModelID string `json:"authorization_model_id,omitempty"`
}

type FGAWriteResponse struct {
	Writes  []interface{} `json:"writes,omitempty"`
	Deletes []interface{} `json:"deletes,omitempty"`
}

// Store for created projects (in-memory)
var projects = make(map[string]*ProjectResponse)

func healthHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]string{
		"status": "ok",
	})
}

func createProjectHandler(w http.ResponseWriter, r *http.Request) {
	var req ProjectRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, fmt.Sprintf("Invalid request body: %v", err), http.StatusBadRequest)
		return
	}

	// Generate a unique ID for the project
	projectUID := uuid.New().String()

	// Create response
	resp := &ProjectResponse{
		UID:         projectUID,
		Slug:        req.Slug,
		Name:        req.Name,
		Description: req.Description,
		Public:      req.Public,
		ParentUID:   req.ParentUID,
	}

	// Store the project
	projects[req.Slug] = resp

	log.Printf("Created project: slug=%s, uid=%s, name=%s", req.Slug, projectUID, req.Name)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(resp)
}

func getProjectHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	slug := vars["slug"]

	project, exists := projects[slug]
	if !exists {
		http.Error(w, "Project not found", http.StatusNotFound)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(project)
}

func listProjectsHandler(w http.ResponseWriter, r *http.Request) {
	projectList := make([]*ProjectResponse, 0, len(projects))
	for _, project := range projects {
		projectList = append(projectList, project)
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"projects": projectList,
		"total":    len(projectList),
	})
}

func fgaWriteHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	storeID := vars["store_id"]

	var req FGAWriteRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, fmt.Sprintf("Invalid request body: %v", err), http.StatusBadRequest)
		return
	}

	// Log the write operation
	if req.Writes != nil {
		for _, tuple := range req.Writes.TupleKeys {
			log.Printf("FGA Write: store=%s, user=%s, relation=%s, object=%s",
				storeID, tuple.User, tuple.Relation, tuple.Object)
		}
	}

	if req.Deletes != nil {
		for _, tuple := range req.Deletes.TupleKeys {
			log.Printf("FGA Delete: store=%s, user=%s, relation=%s, object=%s",
				storeID, tuple.User, tuple.Relation, tuple.Object)
		}
	}

	// Return success response
	resp := FGAWriteResponse{
		Writes:  []interface{}{},
		Deletes: []interface{}{},
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(resp)
}

func corsMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS, PATCH")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")

		if r.Method == "OPTIONS" {
			w.WriteHeader(http.StatusOK)
			return
		}

		next.ServeHTTP(w, r)
	})
}

func loggingMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		log.Printf("%s %s %s", r.RemoteAddr, r.Method, r.URL.Path)
		next.ServeHTTP(w, r)
	})
}

func authMiddleware(apiKey string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			// Get Authorization header
			authHeader := r.Header.Get("Authorization")
			if authHeader == "" {
				http.Error(w, "Missing Authorization header", http.StatusUnauthorized)
				return
			}

			// Check for Bearer token format
			if !strings.HasPrefix(authHeader, "Bearer ") {
				http.Error(w, "Invalid Authorization header format. Expected: Bearer <token>", http.StatusUnauthorized)
				return
			}

			// Extract token
			token := strings.TrimPrefix(authHeader, "Bearer ")
			if token != apiKey {
				http.Error(w, "Invalid API key", http.StatusUnauthorized)
				return
			}

			next.ServeHTTP(w, r)
		})
	}
}

func getEnv(key, defaultValue string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return defaultValue
}

func main() {
	// Configuration flags
	host := flag.String("host", getEnv("MOCK_SERVER_HOST", "0.0.0.0"), "Host to bind to")
	port := flag.String("port", getEnv("MOCK_SERVER_PORT", "8080"), "Port to bind to")
	serviceMode := flag.String("service", getEnv("SERVICE_MODE", "all"), "Service mode: 'projects', 'fga', or 'all'")
	apiKey := flag.String("api-key", getEnv("LFX_API_KEY", "mock-api-key"), "API key for authorization (default: mock-api-key)")
	flag.Parse()

	r := mux.NewRouter()

	// Health check (always available, no auth required)
	r.HandleFunc("/health", healthHandler).Methods("GET")

	endpoints := []string{}

	// Create a subrouter for protected endpoints (Projects API)
	protected := r.PathPrefix("/").Subrouter()

	// Projects API (requires authorization)
	if *serviceMode == "projects" || *serviceMode == "all" {
		r.HandleFunc("/projects", listProjectsHandler).Methods("GET")
		protected.HandleFunc("/projects", createProjectHandler).Methods("POST")
		protected.HandleFunc("/projects/{slug}", getProjectHandler).Methods("GET")
		endpoints = append(endpoints, "POST   /projects (auth required)", "GET    /projects", "GET    /projects/{slug} (auth required)")
	}

	// Apply auth middleware to protected routes only
	protected.Use(authMiddleware(*apiKey))

	// OpenFGA API (no authorization required)
	if *serviceMode == "fga" || *serviceMode == "all" {
		r.HandleFunc("/stores/{store_id}/write", fgaWriteHandler).Methods("POST")
		endpoints = append(endpoints, "POST   /stores/{store_id}/write (no auth required)")
	}

	// Apply general middleware to all routes
	handler := loggingMiddleware(corsMiddleware(r))

	addr := fmt.Sprintf("%s:%s", *host, *port)
	log.Printf("Mock server starting on %s (mode: %s)", addr, *serviceMode)
	log.Printf("API Key (for Projects API): %s", *apiKey)
	log.Printf("Endpoints:")
	log.Printf("  - GET    /health (no auth required)")
	for _, endpoint := range endpoints {
		log.Printf("  - %s", endpoint)
	}

	if err := http.ListenAndServe(addr, handler); err != nil {
		log.Fatal(err)
	}
}
