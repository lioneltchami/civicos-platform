# CivicOS API VPS Migration

This runbook moves the public CivicOS API for `api.civicosbb.ca` off the Mac
and onto a permanent VPS while preserving the existing Cloudflare Tunnel and
hostname.

## Recommended target

- Ubuntu 24.04 LTS
- 2 vCPU minimum
- 4 GB RAM minimum
- 80 GB SSD minimum
- Canadian region if available

## Preferred architecture

Keep `api.civicosbb.ca` behind the existing Cloudflare Tunnel and move only the
tunnel connector and Docker workload to the VPS.

Why this is the right path:

- No DNS cutover is required if the same tunnel is reused
- The origin server does not need public inbound `80/443`
- TLS stays managed at Cloudflare
- The deployment becomes reproducible through Docker Compose instead of a
  laptop-bound background service

## Shared-host note

If the VPS already runs another reverse proxy such as Coolify's Traefik on
public port `80`, do not bind CivicOS directly to `0.0.0.0:80`.

For a shared host, set:

```bash
CIVICOS_HOST_HTTP_BIND=127.0.0.1
CIVICOS_HOST_HTTP_PORT=28080
```

Then point the CivicOS-specific `cloudflared` service at
`http://127.0.0.1:28080`.

This is the deployment mode verified on the current Hetzner shared VPS.

## Files used by this migration

- `docker-compose.prod.yml`
- `docker-compose.tunnel.yml`
- `ops/cloudflared/config.example.yml`
- `ops/cloudflared/config.shared-host.example.yml`
- `ops/systemd/cloudflared-civicosapi.service.example`
- `.env.prod`

## What to copy to the VPS

From the current working Mac deployment:

1. The repo checkout
2. `.env.prod`
3. The tunnel credential JSON currently referenced by
   `~/.cloudflared/config.yml`

Do not commit `.env.prod` or the Cloudflare credential JSON.

## VPS bootstrap

Install Docker Engine and the Compose plugin on the VPS, then clone or copy the
repo.

Suggested packages:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
```

Install Docker using the official Docker instructions for Ubuntu, then verify:

```bash
docker --version
docker compose version
```

## Tunnel file preparation

On the VPS, prepare the Cloudflare Tunnel files:

```bash
mkdir -p ops/cloudflared
cp ops/cloudflared/config.example.yml ops/cloudflared/config.yml
```

Copy the existing tunnel credential JSON into:

```bash
ops/cloudflared/credentials.json
```

The checked-in sidecar example points the tunnel at `http://nginx:80`, which is
correct for a dedicated host or a tunnel sidecar attached to the Docker
network.

For a shared host with a host-level `cloudflared` systemd service, use:

```yaml
ingress:
  - hostname: api.civicosbb.ca
    service: http://127.0.0.1:28080
  - service: http_status:404
```

You can start from `ops/cloudflared/config.shared-host.example.yml`.

For a host-level service, install the config at `/etc/cloudflared/civicosapi.yml`
and use `ops/systemd/cloudflared-civicosapi.service.example` as the template
for `/etc/systemd/system/cloudflared-civicosapi.service`.

## Environment requirements

The `.env.prod` file must include at least:

- `DJANGO_SETTINGS_MODULE=config.settings.production`
- `DJANGO_ALLOWED_HOSTS=api.civicosbb.ca,localhost,127.0.0.1`
- `SITE_URL=https://api.civicosbb.ca`
- `CSRF_TRUSTED_ORIGINS=https://api.civicosbb.ca`
- `WAGTAILADMIN_BASE_URL=https://api.civicosbb.ca`
- `SPECTACULAR_PUBLIC=True`
- `DATABASE_URL`
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `REDIS_URL`
- `REDIS_PASSWORD`
- `DJANGO_SECRET_KEY`
- `JWT_PRIVATE_KEY`
- `JWT_PUBLIC_KEY`
- `FERNET_KEYS`
- `VOLUNTEER_SIN_FERNET_KEYS`

## Preflight validation

Before starting containers, verify the combined Compose config resolves:

```bash
docker compose --env-file .env.prod \
  -p civicosapi \
  -f docker-compose.prod.yml \
  -f docker-compose.tunnel.yml \
  config >/dev/null
```

## Launch on the VPS

Start the full stack:

```bash
docker compose --env-file .env.prod \
  -p civicosapi \
  -f docker-compose.prod.yml \
  up -d --build
```

Check container state:

```bash
docker compose --env-file .env.prod \
  -p civicosapi \
  -f docker-compose.prod.yml \
  ps
```

## Local server verification on the VPS

Verify the origin before cutting over the Mac connector:

```bash
curl -s -o /dev/null -w "live:%{http_code}\n" \
  -H "X-Forwarded-Proto: https" \
  -H "Host: api.civicosbb.ca" \
  http://127.0.0.1:28080/health/live/

curl -s -o /dev/null -w "ready:%{http_code}\n" \
  -H "X-Forwarded-Proto: https" \
  -H "Host: api.civicosbb.ca" \
  http://127.0.0.1:28080/health/ready/

curl -s -o /dev/null -w "schema:%{http_code}\n" \
  -H "X-Forwarded-Proto: https" \
  -H "Host: api.civicosbb.ca" \
  http://127.0.0.1:28080/api/v1/schema/

curl -s -o /dev/null -w "docs:%{http_code}\n" \
  -H "X-Forwarded-Proto: https" \
  -H "Host: api.civicosbb.ca" \
  http://127.0.0.1:28080/api/v1/docs/
```

Expected result: `200` for all four routes.

Avoid `HEAD` checks for `/health/live/` and `/health/ready/` here. The current
deployment answers `GET` with `200`, while `HEAD` may return `405`.

## Public cutover sequence

Because the hostname already points at the existing tunnel, the safest cutover
is:

1. Start the VPS stack and verify local origin responses
2. Start the VPS tunnel connector
2. Confirm Cloudflare sees the new connector
3. Confirm `https://api.civicosbb.ca/api/v1/schema/` returns `200`
4. Confirm `https://api.civicosbb.ca/api/v1/docs/` returns `200`
5. Stop the Mac-hosted connector

The public hostname should remain unchanged throughout.

If you want a zero-surprise cutover on a shared host, create the
`cloudflared-civicosapi.service` unit and leave it disabled until the local VPS
checks return the expected responses.

## How to confirm Cloudflare is using the VPS connector

From the VPS or another trusted shell with `cloudflared` access:

```bash
cloudflared tunnel info civicosapi
```

You want to see a fresh connector entry associated with the VPS host. After the
Mac connector is stopped, the old Mac-origin connector should disappear.

Do not run both the Mac connector and the VPS connector long term against
different origin datasets. That creates non-deterministic public behavior
because Cloudflare can send different requests to different active connectors.

## Mac shutdown after successful cutover

Once the VPS serves the public API correctly, stop the Mac connector and local
stack:

```bash
brew services stop cloudflared
docker compose -p civicosapi -f docker-compose.prod.yml down
```

Do this only after the public hostname has been verified against the VPS-backed
tunnel.

## Rollback

If the VPS deployment fails after public verification:

1. Restart the Mac connector
2. Bring the Mac Docker stack back up if needed
3. Stop the VPS connector
4. Re-test `https://api.civicosbb.ca/api/v1/schema/`

Because the tunnel hostname is unchanged, rollback is just connector
replacement, not DNS replacement.

## Hardening after migration

After the VPS becomes the permanent runtime:

1. Enable automatic OS security updates
2. Configure off-host backups for PostgreSQL and media
3. Restrict inbound firewall rules to SSH only if using Cloudflare Tunnel as the
   only public entry path
4. Install container log shipping or host monitoring
5. Document the exact location of `.env.prod` and the Cloudflare credential JSON
6. Verify Docker restart behavior across a host reboot

## Important note

This migration path keeps the current Cloudflare Tunnel model because it is the
fastest, lowest-risk way to get the API off the Mac. Moving later to direct
origin DNS, Caddy, or a load balancer is a separate infrastructure decision.
