"""
Gunicorn configuration for govstack production deployment.

Worker tuning:
- WEB_CONCURRENCY env var overrides automatic worker count
- gthread worker handles I/O-bound work (S3, SMTP, GC Notify API)
- threads=2 provides concurrency without asyncio overhead
- timeout=120s accommodates file uploads and slow external APIs
"""
import multiprocessing
import os

# ---- Binding ----
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")

# ---- Workers ----
workers = int(os.environ.get("WEB_CONCURRENCY", multiprocessing.cpu_count() * 2 + 1))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "2"))

# ---- Timeouts ----
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", "5"))

# ---- Logging ----
accesslog = "-"   # stdout — collected by container runtime
errorlog = "-"    # stderr
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
access_log_format = (
    '{"remote_addr":"%(h)s","method":"%(m)s","path":"%(U)s",'
    '"status":%(s)s,"response_bytes":%(b)s,"duration_us":%(D)s}'
)

# ---- Worker lifecycle ----
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", "1000"))
max_requests_jitter = int(os.environ.get("GUNICORN_MAX_REQUESTS_JITTER", "100"))

# ---- Trusted proxies (nginx) ----
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "*")
proxy_protocol = False
