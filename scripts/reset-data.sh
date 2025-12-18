#!/bin/bash

# Script to reset all data in NATS KV buckets and OpenSearch
# This clears projects, committees, meetings, and search indices
#
# Usage: ./reset-data.sh

NAMESPACE="lfx"
NATS_BOX_POD=""
OPENSEARCH_POD="opensearch-cluster-master-0"

# Find the NATS box pod
find_nats_box() {
    NATS_BOX_POD=$(kubectl get pods -n $NAMESPACE --no-headers -o custom-columns=":metadata.name" 2>/dev/null | grep nats-box | head -1)
    if [ -z "$NATS_BOX_POD" ]; then
        echo "❌ Could not find nats-box pod"
        return 1
    fi
    echo "Found NATS box pod: $NATS_BOX_POD"
    return 0
}

# Clear and recreate NATS KV buckets
clear_nats_buckets() {
    echo ""
    echo "🗑️  Clearing NATS KV buckets..."

    # All data buckets (excluding authelia-users and authelia-email-otp which are auth-related)
    for bucket in projects project-settings committees committee-settings committee-members \
        meetings meeting-settings meeting-registrants meeting-rsvps meeting-attachments-metadata \
        past-meetings past-meeting-participants past-meeting-recordings past-meeting-transcripts \
        past-meeting-summaries past-meeting-attachments-metadata fga-sync-cache; do
        echo "  Clearing bucket: $bucket"
        kubectl exec -n $NAMESPACE $NATS_BOX_POD -- nats kv rm -f $bucket 2>/dev/null || true
        kubectl exec -n $NAMESPACE $NATS_BOX_POD -- nats kv add $bucket >/dev/null 2>&1
        if [ $? -eq 0 ]; then
            echo "  ✓ Recreated bucket: $bucket"
        else
            echo "  ✗ Failed to recreate bucket: $bucket"
        fi
    done

    echo "✅ NATS KV buckets cleared"
}

# Clear OpenSearch indices and recreate resources mapping
clear_opensearch() {
    echo ""
    echo "🗑️  Clearing OpenSearch indices..."

    # Delete all indices
    kubectl exec -n $NAMESPACE $OPENSEARCH_POD -- curl -s -X DELETE "http://localhost:9200/_all" >/dev/null 2>&1

    if [ $? -eq 0 ]; then
        echo "  ✓ Deleted all indices"
    else
        echo "  ✗ Failed to delete indices"
        return 1
    fi

    # Recreate resources index with mapping
    echo "  Creating resources index with mapping..."

    kubectl exec -n $NAMESPACE $OPENSEARCH_POD -- curl -s -X PUT "http://localhost:9200/resources" \
        -H "Content-Type: application/json" \
        -d '{
  "settings": {
    "number_of_replicas": 0
  },
  "mappings": {
    "properties": {
      "object_ref": { "type": "keyword" },
      "object_type": { "type": "keyword" },
      "object_id": { "type": "keyword" },
      "parent_refs": { "type": "keyword" },
      "sort_name": { "type": "keyword" },
      "name_and_aliases": { "type": "search_as_you_type" },
      "tags": { "type": "keyword" },
      "public": { "type": "boolean" },
      "access_check_query": { "type": "keyword" },
      "history_check_query": { "type": "keyword" },
      "latest": { "type": "boolean" },
      "created_at": { "type": "date" },
      "created_by": { "type": "keyword" },
      "created_by_principals": { "type": "keyword" },
      "created_by_emails": { "type": "keyword" },
      "updated_at": { "type": "date" },
      "updated_by": { "type": "keyword" },
      "updated_by_principals": { "type": "keyword" },
      "updated_by_emails": { "type": "keyword" },
      "deleted_at": { "type": "date" },
      "deleted_by": { "type": "keyword" },
      "deleted_by_principals": { "type": "keyword" },
      "deleted_by_emails": { "type": "keyword" },
      "data": { "type": "flat_object" },
      "fulltext": { "type": "match_only_text" },
      "contacts": {
        "type": "nested",
        "properties": {
          "lfx_principal": { "type": "search_as_you_type" },
          "name": { "type": "search_as_you_type" },
          "emails": { "type": "search_as_you_type" },
          "bot": { "type": "boolean" },
          "profile": { "type": "flat_object" }
        }
      },
      "v1_data": { "type": "flat_object" }
    }
  }
}' >/dev/null 2>&1

    if [ $? -eq 0 ]; then
        echo "  ✓ Created resources index with mapping"
    else
        echo "  ✗ Failed to create resources index"
        return 1
    fi

    echo "✅ OpenSearch indices cleared and recreated"
}

# Restart query service to clear cache
restart_query_service() {
    echo ""
    echo "🔄 Restarting query service..."

    kubectl rollout restart deployment lfx-v2-query-service -n $NAMESPACE >/dev/null 2>&1
    kubectl rollout status deployment lfx-v2-query-service -n $NAMESPACE --timeout=120s >/dev/null 2>&1

    if [ $? -eq 0 ]; then
        echo "✅ Query service restarted"
    else
        echo "⚠️  Query service restart timed out"
    fi
}

# Delete project service pod to clear cache
delete_project_service_pod() {
    echo ""
    echo "🗑️  Deleting project service pod..."

    PROJECT_POD=$(kubectl get pods -A --no-headers 2>/dev/null | grep project-service | grep -v Terminating | awk '{print $2}' | head -1)

    if [ -z "$PROJECT_POD" ]; then
        echo "⚠️  Could not find project service pod"
        return 1
    fi

    echo "  Found pod: $PROJECT_POD"
    kubectl delete pod $PROJECT_POD -n $NAMESPACE >/dev/null 2>&1

    if [ $? -eq 0 ]; then
        echo "✅ Project service pod deleted"
    else
        echo "⚠️  Failed to delete project service pod"
    fi
}

# Main
echo "========================================="
echo "  LFX Data Reset Script"
echo "========================================="

find_nats_box
if [ $? -ne 0 ]; then
    exit 1
fi

clear_nats_buckets
clear_opensearch
restart_query_service
delete_project_service_pod

echo ""
echo "========================================="
echo "✅ All data cleared!"
echo "========================================="
echo ""
echo "Cleared:"
echo "  - NATS KV: projects, project-settings, committees,"
echo "             committee-settings, committee-members,"
echo "             meetings, meeting-settings, meeting-registrants,"
echo "             meeting-rsvps, meeting-attachments-metadata,"
echo "             past-meetings, past-meeting-participants,"
echo "             past-meeting-recordings, past-meeting-transcripts,"
echo "             past-meeting-summaries, past-meeting-attachments-metadata,"
echo "             fga-sync-cache"
echo "  - OpenSearch: all indices (resources recreated)"
echo "  - Query service cache (restarted)"
echo "  - Project service pod (deleted)"
echo ""
echo "Preserved:"
echo "  - NATS KV: authelia-users, authelia-email-otp (auth data)"
