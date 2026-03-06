# Observability Stack - Full Documentation
## Metrics + Logs + Traces cho Production Infrastructure

---

## I. KIẾN TRÚC TỔNG THỂ

```
                        ┌──────────────────────────────────────────────────────┐
                        │                   CLIENT / BROWSER                   │
                        └──────────────────────┬───────────────────────────────┘
                                               │ HTTPS
                        ┌──────────────────────▼───────────────────────────────┐
                        │              NGINX / REVERSE PROXY                   │
                        │  - JSON access log (trace_id, request_id)           │
                        │  - Propagate: traceparent, baggage headers           │
                        └────┬──────────────────────────────────────┬──────────┘
                             │                                      │
              ┌──────────────▼──────────┐            ┌─────────────▼──────────┐
              │     PORTAL SERVICE      │            │      API SERVICE        │
              │  - OTel SDK auto-instr  │──────────▶│  - OTel SDK auto-instr  │
              │  - Structured JSON logs │            │  - Prometheus /metrics  │
              │  - /metrics endpoint    │            │  - Structured JSON logs │
              └─────────────────────────┘            └────────────┬────────────┘
                                                                  │
                                                     ┌────────────▼────────────┐
                                                     │    DATABASE (MySQL/PG)  │
                                                     │  - mysqld_exporter      │
                                                     │  - Slow query log       │
                                                     └─────────────────────────┘

TELEMETRY FLOW:

TRACES:
  App ──OTLP gRPC──▶ OTel Collector ──▶ Tempo ──▶ Grafana (TraceQL)

METRICS:
  App /metrics ◀──scrape── Prometheus ──▶ Grafana (PromQL)
  Node Exporter ◀──scrape── Prometheus
  Various exporters ◀──scrape── Prometheus
  Prometheus ──alert──▶ Alertmanager ──▶ Email/Telegram

LOGS:
  App log files ──▶ Filebeat ──▶ Logstash (parse/enrich) ──▶ Elasticsearch ──▶ Kibana
  Nginx logs ──▶ Filebeat ──▶ Logstash ──▶ Elasticsearch

CORRELATION:
  trace_id liên kết: Grafana Tempo ↔ Kibana Elasticsearch
```

---

## II. DANH SÁCH THÀNH PHẦN

| Component | Version | Port | Vai trò |
|-----------|---------|------|---------|
| Prometheus | 2.51 | 9090 | Thu thập và lưu metrics |
| Alertmanager | 0.27 | 9093 | Route và gửi alert |
| Grafana | 10.4 | 3000 | UI chính: metrics + traces |
| Node Exporter | 1.7 | 9100 | OS metrics (CPU, RAM, Disk...) |
| Blackbox Exporter | 0.24 | 9115 | HTTP/TCP endpoint probing |
| OTel Collector | 0.98 | 4317/4318 | Thu thập và forward traces |
| Tempo | 2.4 | 3200 | Lưu trữ distributed traces |
| Elasticsearch | 8.13 | 9200 | Lưu trữ logs |
| Logstash | 8.13 | 5044 | Parse và enrich logs |
| Filebeat | 8.13 | - | Thu thập log files |
| Kibana | 8.13 | 5601 | UI tìm kiếm log |
| nginx-exporter | 1.1 | 9113 | Nginx metrics |
| mysqld-exporter | - | 9104 | MySQL metrics |
| redis-exporter | - | 9121 | Redis metrics |

---

## III. THỨ TỰ TRIỂN KHAI

### Bước 1: Infrastructure cơ bản
```bash
# Clone/copy toàn bộ thư mục observability
cd /opt/observability

# Start storage layer trước
docker compose up -d elasticsearch
# Đợi ES healthy (30-60s)
docker compose up -d prometheus

echo "Đợi Elasticsearch sẵn sàng..."
sleep 30
```

### Bước 2: Setup Elasticsearch
```bash
bash elk/elasticsearch/setup-elasticsearch.sh
```

### Bước 3: Start toàn bộ stack
```bash
docker compose up -d
```

### Bước 4: Verify từng service
```bash
# Prometheus
curl http://localhost:9090/-/healthy

# Alertmanager
curl http://localhost:9093/-/healthy

# Grafana
curl http://localhost:3000/api/health

# Elasticsearch
curl -u elastic:ElasticPass123! http://localhost:9200/_cluster/health

# Kibana
curl http://localhost:5601/api/status

# Tempo
curl http://localhost:3200/ready

# OTel Collector
curl http://localhost:13133/
```

### Bước 5: Cài Node Exporter trên mỗi server
```bash
# Trên MỖI server cần monitor (portal, api, db servers):
bash scripts/install-node-exporter.sh
```

### Bước 6: Instrument application
```bash
# Python app: xem docs/example-app-instrumentation.py
# Node.js: xem phần Node.js trong file đó
# Cấu hình env vars:
export OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
export SERVICE_NAME=api-service
export ENV=production
```

---

## IV. KIỂM TRA TỪNG THÀNH PHẦN

### Test Prometheus scraping
```bash
# Xem targets đang scrape
curl http://localhost:9090/api/v1/targets | python3 -m json.tool

# Query thử metric
curl 'http://localhost:9090/api/v1/query?query=up'

# Xem rules
curl http://localhost:9090/api/v1/rules
```

### Test Alertmanager
```bash
# Gửi test alert
curl -X POST http://localhost:9093/api/v2/alerts \
  -H "Content-Type: application/json" \
  -d '[{
    "labels": {"alertname":"TestAlert","severity":"warning","env":"test"},
    "annotations": {"summary":"Test alert from setup","description":"Testing alertmanager"},
    "startsAt": "2024-01-01T00:00:00Z",
    "endsAt": "2024-12-31T23:59:59Z"
  }]'
```

### Test OTel Tracing
```bash
# Gửi test trace
curl -X POST http://localhost:4320/v1/traces \
  -H "Content-Type: application/json" \
  -d '{
    "resourceSpans": [{
      "resource": {
        "attributes": [{"key":"service.name","value":{"stringValue":"test-service"}}]
      },
      "scopeSpans": [{
        "spans": [{
          "traceId": "5b8efff798038103d269b633813fc60c",
          "spanId": "eee19b7ec3c1b174",
          "name": "test-span",
          "kind": 1,
          "startTimeUnixNano": "1672531200000000000",
          "endTimeUnixNano": "1672531200500000000"
        }]
      }]
    }]
  }'

# Kiểm tra trace trong Tempo
curl "http://localhost:3200/api/search?tags=service.name%3Dtest-service&limit=5"
```

### Test Elasticsearch
```bash
# Kiểm tra index
curl -u elastic:ElasticPass123! http://localhost:9200/_cat/indices?v

# Test tìm log theo trace_id
curl -u elastic:ElasticPass123! \
  -X GET "http://localhost:9200/logs-app-*/_search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": {
      "term": { "trace_id": "YOUR_TRACE_ID_HERE" }
    },
    "sort": [{"@timestamp": "desc"}],
    "size": 20
  }'
```

---

## V. CORRELATION: Metrics → Traces → Logs

### Workflow khi có sự cố:

```
1. Grafana Alert hoặc Dashboard báo API latency tăng cao
   ↓
2. Mở Grafana → Dashboard "API Overview"
   → Xem panel "p95 Response Time" theo service
   ↓
3. Click vào data point → "Explore" → Chọn Tempo datasource
   → Tìm traces có duration cao nhất trong khoảng thời gian đó
   ↓
4. Chọn 1 trace → Xem waterfall:
   - portal-service → api-service: 200ms
   - api-service → db.query.orders: 2000ms  ← BOTTLENECK!
   - db.query.orders có attribute: db.statement
   ↓
5. Copy trace_id từ Tempo
   ↓
6. Mở Kibana → Discover
   → Filter: trace_id: "abc123def456..."
   → Xem toàn bộ log của request đó qua tất cả services
   ↓
7. Xem log error message, stack trace, DB slow query detail
```

### Kibana queries hữu ích:
```
# Tìm theo trace_id
trace_id:"abc123def456"

# Tìm lỗi trong 1h qua
log_level:"ERROR" AND @timestamp:[now-1h TO now]

# Tìm API chậm (>2s)
log_type:"application" AND response_time_ms:>2000

# Nginx 5xx
log_type:"nginx_access" AND http_status_code:>=500

# Slow DB query
log_type:"mysql_slow" AND query_time:>1.0

# Tìm lỗi của 1 service cụ thể
service:"api-service" AND log_level:"ERROR"

# Correlate user journey
user_id:"12345" AND @timestamp:[now-1h TO now]
```

---

## VI. GRAFANA DASHBOARDS CẦN XÂY

### 1. System Overview Dashboard
**Panels:**
- CPU Usage (%) - by host - Line chart
- RAM Usage (%) - by host - Line chart
- Disk Usage (%) - by host, mount - Bar gauge
- Disk IO Read/Write (MB/s) - Line chart
- Network In/Out (MB/s) - Line chart
- Load Average (1m, 5m, 15m) - Line chart
- Swap Usage - Stat panel
- Host Status (up/down) - Table

**PromQL examples:**
```promql
# CPU per host
100 - (avg by(hostname) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)

# RAM available
node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes * 100

# Disk usage
(node_filesystem_size_bytes - node_filesystem_avail_bytes) / node_filesystem_size_bytes * 100
```

### 2. Application Overview Dashboard
**Panels:**
- Request Rate (req/s) - by service
- Error Rate (%) - by service - threshold red >5%
- p50/p95/p99 Response Time - by service
- Active Requests - Gauge
- Request Volume heatmap
- Top 10 slowest endpoints - Table
- Error breakdown by status code - Pie chart

**PromQL examples:**
```promql
# Request rate
sum by(service) (rate(http_requests_total[5m]))

# Error rate %
sum by(service) (rate(http_requests_total{status=~"5.."}[5m]))
/ sum by(service) (rate(http_requests_total[5m])) * 100

# p95 latency
histogram_quantile(0.95, sum by(le, service) (rate(http_request_duration_seconds_bucket[5m])))
```

### 3. Nginx Dashboard
**Panels:**
- Total Requests/s
- 2xx/3xx/4xx/5xx breakdown
- Response time p50/p95/p99
- Active connections
- Bytes in/out
- Top URIs by request count
- Geographic map (with GeoIP)

### 4. Database Dashboard
**Panels:**
- MySQL connections (current/max)
- QPS (queries per second)
- Slow queries/s
- Buffer pool hit rate
- Table lock waits
- Replication lag (if slave)
- InnoDB buffer pool usage

### 5. Service Map (Tempo)
- Enable trong Grafana Explore → Tempo → Service Graph
- Shows: request rate, error rate, latency between services
- Click service → drill into traces

### 6. Trace Explorer
- Grafana Explore → Tempo datasource
- Search by: service name, duration, tags, error
- TraceQL examples:
  ```
  { .service.name = "api-service" && duration > 2s }
  { .http.status_code >= 500 }
  { .db.system = "mysql" && duration > 1s }
  ```

---

## VII. BẢO MẬT VÀ TỐI ƯU

### Security checklist:
```bash
# 1. Đặt tất cả monitoring UIs sau Nginx + basic auth
htpasswd -c /etc/nginx/.htpasswd admin

# 2. Tắt anonymous access Grafana
GF_AUTH_ANONYMOUS_ENABLED=false

# 3. Thay đổi default passwords
# - Grafana admin
# - Elasticsearch elastic user
# - Kibana kibana_system user

# 4. Firewall: chỉ mở port cần thiết ra ngoài
ufw allow 80/tcp   # HTTP
ufw allow 443/tcp  # HTTPS
# Tất cả port monitoring chỉ allow nội bộ

# 5. TLS cho Elasticsearch trong production
# - Tạo self-signed hoặc dùng cert thật
# - Bật xpack.security.http.ssl.enabled=true

# 6. Node Exporter: chỉ bind localhost hoặc dùng iptables
# --web.listen-address=127.0.0.1:9100
```

### Sizing khuyến nghị:

| Component | RAM | CPU | Disk | Notes |
|-----------|-----|-----|------|-------|
| Prometheus | 4-8GB | 2-4 | 100GB+ SSD | 30d retention |
| Grafana | 1-2GB | 1-2 | 10GB | Stateless mostly |
| Alertmanager | 512MB | 1 | 5GB | |
| OTel Collector | 1-2GB | 2 | 10GB | |
| Tempo | 2-4GB | 2-4 | 200GB+ | 14d retention |
| Elasticsearch | 8-16GB | 4-8 | 500GB+ SSD | 90d retention |
| Logstash | 2-4GB | 2-4 | 20GB | |
| Kibana | 1-2GB | 1-2 | 10GB | |

### Lỗi phổ biến và cách tránh:

1. **Elasticsearch OOM**: Set `ES_JAVA_OPTS=-Xms4g -Xmx4g` không quá 50% RAM server
2. **Prometheus disk đầy**: Set retention `--storage.tsdb.retention.size=20GB`
3. **Filebeat quá tải**: Tăng `bulk_max_size` và `worker` trong output
4. **OTel Collector drop spans**: Tăng `queue_size` và `num_consumers`
5. **Tempo không nhận traces**: Verify `insecure: true` nếu không có TLS
6. **Alert storm**: Cấu hình `inhibit_rules` và `group_interval` hợp lý
7. **Log format không nhất quán**: Enforce JSON log format ở tất cả services

---

## VIII. CHECKLIST NGHIỆM THU

### Metrics:
- [ ] Prometheus scrape tất cả targets (up == 1)
- [ ] Node Exporter báo cáo CPU, RAM, Disk, Network
- [ ] Application /metrics endpoint hoạt động
- [ ] Alertmanager nhận và route alerts
- [ ] Test alert gửi thành công qua Email/Telegram
- [ ] Grafana kết nối được Prometheus

### Logs:
- [ ] Filebeat thu log từ /var/log và Docker containers
- [ ] Logstash parse JSON log thành công
- [ ] Elasticsearch nhận được dữ liệu
- [ ] Index có mapping đúng (trace_id là keyword)
- [ ] Kibana hiển thị log
- [ ] Có thể tìm log theo trace_id

### Traces:
- [ ] OTel Collector nhận OTLP traces
- [ ] Tempo lưu traces thành công
- [ ] Grafana Explore → Tempo tìm được trace
- [ ] Waterfall hiển thị đủ spans: portal → api → db
- [ ] trace_id khớp giữa Tempo và Kibana

### Correlation:
- [ ] trace_id có trong log của tất cả services
- [ ] trace_id được propagate qua HTTP headers
- [ ] Grafana link từ metric sang trace hoạt động
- [ ] Kibana tìm được log theo trace_id từ Tempo

### Alerts:
- [ ] HostDown alert fire khi stop node_exporter
- [ ] HighCPUUsage alert fire khi chạy stress test
- [ ] DiskSpaceCritical alert fire khi disk > 90%
- [ ] ServiceDown alert fire khi stop application
- [ ] Email/Telegram nhận được alert message

---

## IX. MANAGEMENT COMMANDS

```bash
# Start toàn bộ stack
docker compose up -d

# Dừng toàn bộ stack
docker compose down

# Restart 1 service
docker compose restart prometheus

# Xem logs
docker compose logs -f grafana
docker compose logs -f otel-collector

# Reload Prometheus config (không restart)
curl -X POST http://localhost:9090/-/reload

# Reload Alertmanager config
curl -X POST http://localhost:9093/-/reload

# Backup Grafana dashboards
docker exec grafana grafana-cli admin export-dashboards

# Elasticsearch snapshot
curl -u elastic:ElasticPass123! \
  -X PUT "localhost:9200/_snapshot/backup/snapshot_$(date +%Y%m%d)" \
  -H "Content-Type: application/json" \
  -d '{"indices": "logs-*", "ignore_unavailable": true}'
```
