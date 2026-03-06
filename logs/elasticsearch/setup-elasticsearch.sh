#!/bin/bash
# ============================================================
# setup-elasticsearch.sh
# Initialize Elasticsearch: ILM policies, index templates
# Run ONCE after Elasticsearch starts
# ============================================================

ES_HOST="${ES_HOST:-http://localhost:9200}"
ES_USER="${ES_USER:-elastic}"
ES_PASS="${ES_PASS:-ElasticPass123!}"

AUTH="-u ${ES_USER}:${ES_PASS}"

echo "======================================================"
echo "Setting up Elasticsearch for Observability Stack"
echo "Host: $ES_HOST"
echo "======================================================"

# Wait for Elasticsearch to be ready
echo "Waiting for Elasticsearch..."
until curl -s $AUTH "${ES_HOST}/_cluster/health" | grep -q '"status"'; do
  sleep 5
done
echo "Elasticsearch is ready!"

# ============================================================
# 1. ILM Policy - Log retention
# ============================================================
echo ""
echo "Creating ILM Policy..."

curl -s -X PUT $AUTH "${ES_HOST}/_ilm/policy/logs-ilm-policy" \
  -H "Content-Type: application/json" \
  -d '{
    "policy": {
      "phases": {
        "hot": {
          "min_age": "0ms",
          "actions": {
            "rollover": {
              "max_primary_shard_size": "10gb",
              "max_age": "1d",
              "max_docs": 10000000
            },
            "set_priority": { "priority": 100 }
          }
        },
        "warm": {
          "min_age": "7d",
          "actions": {
            "shrink": { "number_of_shards": 1 },
            "forcemerge": { "max_num_segments": 1 },
            "set_priority": { "priority": 50 },
            "readonly": {}
          }
        },
        "cold": {
          "min_age": "30d",
          "actions": {
            "set_priority": { "priority": 0 },
            "freeze": {}
          }
        },
        "delete": {
          "min_age": "90d",
          "actions": {
            "delete": {}
          }
        }
      }
    }
  }'

echo " ✓ ILM Policy created"

# ============================================================
# 2. Component Templates
# ============================================================
echo ""
echo "Creating component templates..."

# Settings template
curl -s -X PUT $AUTH "${ES_HOST}/_component_template/logs-settings" \
  -H "Content-Type: application/json" \
  -d '{
    "template": {
      "settings": {
        "number_of_shards": 2,
        "number_of_replicas": 1,
        "refresh_interval": "5s",
        "index.lifecycle.name": "logs-ilm-policy",
        "index.codec": "best_compression"
      }
    }
  }'

# Mappings template for common log fields
curl -s -X PUT $AUTH "${ES_HOST}/_component_template/logs-mappings" \
  -H "Content-Type: application/json" \
  -d '{
    "template": {
      "mappings": {
        "dynamic": true,
        "dynamic_templates": [
          {
            "strings_as_keyword": {
              "match_mapping_type": "string",
              "mapping": {
                "type": "keyword",
                "ignore_above": 512
              }
            }
          }
        ],
        "properties": {
          "@timestamp":       { "type": "date" },
          "message":          { "type": "text", "fields": { "keyword": { "type": "keyword", "ignore_above": 2048 } } },
          "log_message":      { "type": "text", "fields": { "keyword": { "type": "keyword", "ignore_above": 2048 } } },
          "log_level":        { "type": "keyword" },
          "log_level_num":    { "type": "integer" },
          "log_type":         { "type": "keyword" },
          "service":          { "type": "keyword" },
          "service_name":     { "type": "keyword" },
          "hostname":         { "type": "keyword" },
          "env":              { "type": "keyword" },
          "cluster":          { "type": "keyword" },
          "trace_id":         { "type": "keyword" },
          "span_id":          { "type": "keyword" },
          "request_id":       { "type": "keyword" },
          "user_id":          { "type": "keyword" },
          "http_method":      { "type": "keyword" },
          "http_path":        { "type": "keyword" },
          "http_status_code": { "type": "integer" },
          "response_time_ms": { "type": "float" },
          "response_time":    { "type": "float" },
          "upstream_response_time": { "type": "float" },
          "bytes_sent":       { "type": "long" },
          "client_ip":        { "type": "ip" },
          "error_code":       { "type": "keyword" },
          "error_message":    { "type": "text", "fields": { "keyword": { "type": "keyword", "ignore_above": 1024 } } },
          "user_agent":       { "type": "keyword" },
          "vhost":            { "type": "keyword" },
          "tags":             { "type": "keyword" },
          "query_time":       { "type": "float" },
          "lock_time":        { "type": "float" },
          "rows_sent":        { "type": "integer" },
          "rows_examined":    { "type": "integer" },
          "sql_query":        { "type": "text", "index": false },
          "geoip": {
            "properties": {
              "city_name":    { "type": "keyword" },
              "country_name": { "type": "keyword" },
              "country_code2":{ "type": "keyword" },
              "location":     { "type": "geo_point" }
            }
          }
        }
      }
    }
  }'

echo " ✓ Component templates created"

# ============================================================
# 3. Index Templates
# ============================================================
echo ""
echo "Creating index templates..."

# App logs template
curl -s -X PUT $AUTH "${ES_HOST}/_index_template/logs-app" \
  -H "Content-Type: application/json" \
  -d '{
    "index_patterns": ["logs-app-*"],
    "composed_of": ["logs-settings", "logs-mappings"],
    "priority": 200,
    "data_stream": {},
    "template": {
      "settings": {
        "number_of_shards": 2
      }
    }
  }'

# Nginx logs template
curl -s -X PUT $AUTH "${ES_HOST}/_index_template/logs-nginx" \
  -H "Content-Type: application/json" \
  -d '{
    "index_patterns": ["logs-nginx-*"],
    "composed_of": ["logs-settings", "logs-mappings"],
    "priority": 200,
    "data_stream": {}
  }'

# DB logs template
curl -s -X PUT $AUTH "${ES_HOST}/_index_template/logs-db" \
  -H "Content-Type: application/json" \
  -d '{
    "index_patterns": ["logs-db-*"],
    "composed_of": ["logs-settings", "logs-mappings"],
    "priority": 200,
    "data_stream": {}
  }'

# Catch-all logs template
curl -s -X PUT $AUTH "${ES_HOST}/_index_template/logs-all" \
  -H "Content-Type: application/json" \
  -d '{
    "index_patterns": ["logs-*"],
    "composed_of": ["logs-settings", "logs-mappings"],
    "priority": 100,
    "data_stream": {}
  }'

echo " ✓ Index templates created"

# ============================================================
# 4. Kibana system user password
# ============================================================
echo ""
echo "Setting Kibana system user password..."
curl -s -X POST $AUTH "${ES_HOST}/_security/user/kibana_system/_password" \
  -H "Content-Type: application/json" \
  -d '{"password": "KibanaPass123!"}'

echo " ✓ Kibana user configured"

# ============================================================
# 5. Create useful saved searches / aliases
# ============================================================
echo ""
echo "Creating index aliases..."

# Alias for all logs
curl -s -X PUT $AUTH "${ES_HOST}/_aliases" \
  -H "Content-Type: application/json" \
  -d '{
    "actions": [
      { "add": { "index": "logs-*", "alias": "all-logs" } }
    ]
  }' 2>/dev/null || true

echo ""
echo "======================================================"
echo "✅ Elasticsearch setup complete!"
echo ""
echo "Useful Kibana queries:"
echo "  - Find by trace_id:   trace_id:\"abc123\""
echo "  - Find errors:        log_level:\"ERROR\""
echo "  - Find slow requests: response_time_ms:>1000"
echo "  - Find 5xx:           http_status_code:>=500"
echo "  - Nginx 5xx:          log_type:\"nginx_access\" AND http_status_code:>=500"
echo "======================================================"
