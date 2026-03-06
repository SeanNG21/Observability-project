"""
============================================================
Example: FastAPI Application với Full OpenTelemetry Tracing
Demonstrates: trace_id propagation, span creation, log correlation
============================================================

Install dependencies:
pip install fastapi uvicorn opentelemetry-sdk opentelemetry-api \
  opentelemetry-exporter-otlp opentelemetry-instrumentation-fastapi \
  opentelemetry-instrumentation-sqlalchemy opentelemetry-instrumentation-requests \
  opentelemetry-instrumentation-redis python-json-logger structlog
"""

import os
import time
import uuid
import logging
import structlog
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# OpenTelemetry imports
from opentelemetry import trace, baggage, context
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.propagate import extract, inject
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry import propagate

# Prometheus metrics
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# ============================================================
# PROMETHEUS METRICS DEFINITION
# ============================================================
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code", "service", "env"]
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint", "service", "env"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

ACTIVE_REQUESTS = Gauge(
    "http_active_requests",
    "Currently active HTTP requests",
    ["service"]
)

DB_QUERY_DURATION = Histogram(
    "db_query_duration_seconds",
    "Database query duration",
    ["operation", "table", "service"],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0]
)

EXTERNAL_CALL_DURATION = Histogram(
    "external_call_duration_seconds",
    "External API call duration",
    ["target_service", "endpoint", "status_code"]
)

# ============================================================
# STRUCTURED LOGGING SETUP
# ============================================================
def setup_logging():
    """Configure structured JSON logging with trace_id injection"""

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        level=logging.INFO,
        handlers=[logging.StreamHandler()]
    )

logger = structlog.get_logger()

# ============================================================
# OPENTELEMETRY SETUP
# ============================================================
def setup_tracing(service_name: str, service_version: str = "1.0.0"):
    """Initialize OpenTelemetry tracing"""

    resource = Resource.create({
        SERVICE_NAME: service_name,
        SERVICE_VERSION: service_version,
        "deployment.environment": os.getenv("ENV", "production"),
        "service.namespace": "yourdomain",
        "host.name": os.uname().nodename,
    })

    provider = TracerProvider(resource=resource)

    # OTLP Exporter -> OTel Collector
    otlp_exporter = OTLPSpanExporter(
        endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317"),
        insecure=True,
    )

    provider.add_span_processor(
        BatchSpanProcessor(
            otlp_exporter,
            max_queue_size=2048,
            max_export_batch_size=512,
            export_timeout_millis=30000,
        )
    )

    trace.set_tracer_provider(provider)

    # Setup W3C TraceContext + Baggage propagation
    propagate.set_global_textmap(
        CompositePropagator([
            TraceContextTextMapPropagator(),
            W3CBaggagePropagator(),
        ])
    )

    return trace.get_tracer(service_name)


# ============================================================
# HELPER: Get trace_id from current span
# ============================================================
def get_trace_context() -> dict:
    """Extract current trace_id and span_id for log correlation"""
    span = trace.get_current_span()
    if span and span.is_recording():
        ctx = span.get_span_context()
        trace_id = format(ctx.trace_id, "032x") if ctx.trace_id else None
        span_id = format(ctx.span_id, "016x") if ctx.span_id else None
        return {"trace_id": trace_id, "span_id": span_id}
    return {"trace_id": None, "span_id": None}


def get_tracer():
    return trace.get_tracer(__name__)


# ============================================================
# APPLICATION SETUP
# ============================================================
SERVICE_NAME = os.getenv("SERVICE_NAME", "api-service")
ENV = os.getenv("ENV", "production")

setup_logging()
tracer = setup_tracing(SERVICE_NAME)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("service.start", service=SERVICE_NAME, env=ENV)
    yield
    logger.info("service.stop", service=SERVICE_NAME)

app = FastAPI(
    title="API Service",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "traceparent", "tracestate", "baggage"],
)

# Auto-instrument FastAPI and HTTPX
FastAPIInstrumentor.instrument_app(app)
HTTPXClientInstrumentor().instrument()

# ============================================================
# MIDDLEWARE: Request tracking, metrics, log correlation
# ============================================================
@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    """
    Middleware to:
    1. Extract/generate request_id
    2. Bind trace_id to structured logs
    3. Record Prometheus metrics
    4. Add response headers
    """
    start_time = time.time()

    # Extract or generate request_id
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

    # Get trace context (set by FastAPIInstrumentor)
    trace_ctx = get_trace_context()
    trace_id = trace_ctx.get("trace_id") or request_id

    # Bind context to all logs in this request
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        trace_id=trace_id,
        span_id=trace_ctx.get("span_id"),
        request_id=request_id,
        service=SERVICE_NAME,
        env=ENV,
        method=request.method,
        path=request.url.path,
        client_ip=request.client.host if request.client else "unknown",
    )

    ACTIVE_REQUESTS.labels(service=SERVICE_NAME).inc()

    try:
        response = await call_next(request)
        duration = time.time() - start_time
        status_code = response.status_code

        # Record metrics
        endpoint = request.url.path
        REQUEST_COUNT.labels(
            method=request.method,
            endpoint=endpoint,
            status_code=status_code,
            service=SERVICE_NAME,
            env=ENV
        ).inc()

        REQUEST_LATENCY.labels(
            method=request.method,
            endpoint=endpoint,
            service=SERVICE_NAME,
            env=ENV
        ).observe(duration)

        # Structured access log
        log_fn = logger.warning if status_code >= 400 else logger.info
        log_fn(
            "http.request",
            status_code=status_code,
            duration_ms=round(duration * 1000, 2),
            user_agent=request.headers.get("user-agent", ""),
        )

        # Add trace headers to response
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Trace-ID"] = trace_id

        return response

    except Exception as exc:
        duration = time.time() - start_time
        logger.error(
            "http.request.error",
            error=str(exc),
            error_type=type(exc).__name__,
            duration_ms=round(duration * 1000, 2),
        )
        REQUEST_COUNT.labels(
            method=request.method,
            endpoint=request.url.path,
            status_code=500,
            service=SERVICE_NAME,
            env=ENV
        ).inc()
        raise
    finally:
        ACTIVE_REQUESTS.labels(service=SERVICE_NAME).dec()


# ============================================================
# METRICS ENDPOINT
# ============================================================
@app.get("/metrics", include_in_schema=False)
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


# ============================================================
# EXAMPLE: API endpoint with manual tracing + DB call
# ============================================================
@app.get("/api/v1/orders/{order_id}")
async def get_order(order_id: int, request: Request):
    """
    Example endpoint showing full tracing:
    Portal -> API -> DB
    """
    tracer_inst = get_tracer()

    with tracer_inst.start_as_current_span("get_order") as span:
        span.set_attribute("order.id", order_id)
        span.set_attribute("service.name", SERVICE_NAME)

        trace_ctx = get_trace_context()
        logger.info("order.fetch.start", order_id=order_id)

        # Simulate DB query with tracing
        order_data = await _fetch_order_from_db(order_id)

        if not order_data:
            span.set_status(trace.StatusCode.ERROR, "Order not found")
            logger.warning("order.not_found", order_id=order_id)
            raise HTTPException(status_code=404, detail="Order not found")

        logger.info("order.fetch.success", order_id=order_id, order_status=order_data.get("status"))
        return order_data


async def _fetch_order_from_db(order_id: int) -> Optional[dict]:
    """Simulate a DB query with OpenTelemetry span"""
    tracer_inst = get_tracer()

    with tracer_inst.start_as_current_span("db.query.orders") as span:
        span.set_attribute("db.system", "mysql")
        span.set_attribute("db.name", "orders_db")
        span.set_attribute("db.operation", "SELECT")
        span.set_attribute("db.table", "orders")
        span.set_attribute("db.statement", f"SELECT * FROM orders WHERE id = ?")

        start = time.time()
        try:
            # Simulate DB latency
            import asyncio
            await asyncio.sleep(0.05)

            result = {"id": order_id, "status": "completed", "amount": 150.0}
            duration = time.time() - start

            DB_QUERY_DURATION.labels(
                operation="SELECT",
                table="orders",
                service=SERVICE_NAME
            ).observe(duration)

            span.set_attribute("db.rows_returned", 1)
            logger.info("db.query.success",
                        table="orders",
                        duration_ms=round(duration * 1000, 2),
                        rows=1)
            return result

        except Exception as e:
            span.record_exception(e)
            span.set_status(trace.StatusCode.ERROR, str(e))
            logger.error("db.query.error", error=str(e), table="orders")
            raise


@app.get("/api/v1/users/{user_id}/profile")
async def get_user_profile(user_id: int):
    """Example: calling external/internal service"""
    tracer_inst = get_tracer()

    with tracer_inst.start_as_current_span("get_user_profile") as span:
        span.set_attribute("user.id", user_id)

        # Call another internal service with trace propagation
        headers = {}
        inject(headers)  # Inject W3C traceparent/tracestate

        async with httpx.AsyncClient() as client:
            with tracer_inst.start_as_current_span("http.call.user-service") as http_span:
                http_span.set_attribute("http.method", "GET")
                http_span.set_attribute("http.url", f"http://user-service/internal/users/{user_id}")
                http_span.set_attribute("peer.service", "user-service")

                start = time.time()
                try:
                    # In real code: response = await client.get(url, headers=headers)
                    # Simulated:
                    import asyncio
                    await asyncio.sleep(0.02)
                    result = {"user_id": user_id, "name": "Test User", "email": "test@example.com"}
                    duration = time.time() - start

                    EXTERNAL_CALL_DURATION.labels(
                        target_service="user-service",
                        endpoint=f"/internal/users/{user_id}",
                        status_code=200
                    ).observe(duration)

                    logger.info("service.call.success",
                                target="user-service",
                                duration_ms=round(duration * 1000, 2))
                    return result

                except Exception as e:
                    http_span.record_exception(e)
                    http_span.set_status(trace.StatusCode.ERROR, str(e))
                    logger.error("service.call.error", target="user-service", error=str(e))
                    raise


# ============================================================
# Node.js / Express equivalent (reference)
# ============================================================
"""
// Node.js equivalent - tracing.js (load before app)
const { NodeSDK } = require('@opentelemetry/sdk-node');
const { OTLPTraceExporter } = require('@opentelemetry/exporter-trace-otlp-grpc');
const { getNodeAutoInstrumentations } = require('@opentelemetry/auto-instrumentations-node');
const { Resource } = require('@opentelemetry/resources');
const { SemanticResourceAttributes } = require('@opentelemetry/semantic-conventions');

const sdk = new NodeSDK({
  resource: new Resource({
    [SemanticResourceAttributes.SERVICE_NAME]: process.env.SERVICE_NAME || 'portal-service',
    [SemanticResourceAttributes.SERVICE_VERSION]: '1.0.0',
    'deployment.environment': process.env.ENV || 'production',
  }),
  traceExporter: new OTLPTraceExporter({
    url: process.env.OTEL_EXPORTER_OTLP_ENDPOINT || 'http://otel-collector:4317',
  }),
  instrumentations: [
    getNodeAutoInstrumentations({
      '@opentelemetry/instrumentation-http': { enabled: true },
      '@opentelemetry/instrumentation-express': { enabled: true },
      '@opentelemetry/instrumentation-mysql2': { enabled: true },
      '@opentelemetry/instrumentation-redis': { enabled: true },
    }),
  ],
});

sdk.start();

// In Express middleware - inject trace_id into logs
app.use((req, res, next) => {
  const span = trace.getActiveSpan();
  if (span) {
    const traceId = span.spanContext().traceId;
    req.traceId = traceId;
    res.setHeader('X-Trace-ID', traceId);
    // Add to logger context
    logger.child({ trace_id: traceId, request_id: req.headers['x-request-id'] });
  }
  next();
});
"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081, log_config=None)
