# Deploying barelybooting-server

Target: a Home Assistant OS mini PC with Docker, fronted by a Cloudflare
Tunnel so no port forwarding is needed on the upstream router (UDM Pro).

This guide assumes:
- Home Assistant OS is already running on the host.
- You own `barelybooting.com` and DNS is on Cloudflare.
- You have SSH or terminal access to the HA host (via the Advanced SSH
  & Web Terminal add-on).

## Architecture

```
DOS client  --HTTP POST-->  barelybooting.com (Cloudflare edge, HTTPS + WAF)
                                      |
                                      v
                            Cloudflare Tunnel (outbound only)
                                      |
                                      v
                        cloudflared container (HA mini PC)
                                      |
                                      v
                        barelybooting container :8080
                                      |
                                      v
                       SQLite on named volume (persistent)
```

The tunnel is outbound: nothing on the UDM Pro needs to change. No
port forward, no DDNS, no public IP.

## One-time setup

### 1. Install the Advanced SSH & Web Terminal add-on

Settings > Add-ons > Add-on Store > "Advanced SSH & Web Terminal"
(community add-on). Install, configure auth, start. Open the Web UI to
get a shell.

Docker Compose is bundled with the add-on:

```
docker version
docker compose version
```

If `docker compose` is missing, install Portainer instead (see note at
the bottom) or use the Community add-on "Portainer" for a web UI.

### 2. Create the Cloudflare Tunnel

On the Cloudflare dashboard:

1. Sign in, go to `one.dash.cloudflare.com` (Zero Trust).
2. Networks > Tunnels > "Create a tunnel."
3. Connector type: **Cloudflared**. Name it `barelybooting`.
4. Copy the **tunnel token** shown on the install step. You do NOT
   need to run the install command; the Docker container will use the
   token directly.
5. Click "Next" through the connector step.
6. **Public Hostnames** step: add one route.
   - Subdomain: (blank) or `www`
   - Domain: `barelybooting.com`
   - Path: `/` (leave empty for root)
   - Service type: `HTTP`
   - URL: `barelybooting:8080`
     (this is the docker-compose service name, reachable from the
     `cloudflared` sidecar on the shared `web` network)
7. Save. Cloudflare will auto-create the DNS CNAME.

If you want `barelybooting.com/cerberus/` to be the entry point and
keep a separate landing page on the apex, use a subdomain like
`api.barelybooting.com` instead and point only the intake endpoint at
the tunnel. The current CERBERUS client hard-codes `barelybooting.com`
in `UPLOAD_URL` (`src/upload/upload.c`), so whichever hostname you
pick, make sure the next CERBERUS build matches.

### 3. Enable Cloudflare's edge protections

Cloudflare's tunnel by itself does not apply WAF or rate limiting. Turn
these on (all free tier):

- Security > Bots > **Bot Fight Mode: On**. Blocks known automated
  scrapers cheaply.
- Security > WAF > Managed Rules > **Enable the default managed
  ruleset**. Handles common exploit patterns (WP admin probes, Log4j
  payloads, etc.) at the edge before they reach the tunnel.
- Security > WAF > Custom Rules > "Block common scanner paths":
  ```
  (http.request.uri.path contains ".env") or
  (http.request.uri.path contains ".git") or
  (http.request.uri.path contains "wp-admin") or
  (http.request.uri.path contains "phpmyadmin") or
  (http.request.uri.path contains ".php")
  ```
  Action: Block.
- Security > WAF > Rate limiting rules: **add a rule for
  `/api/v1/submit`** capping POST requests at e.g. 60 per 10 minutes
  per IP. The app itself also rate-limits (30/hour, 5/min), but doing
  it at the edge shields the tunnel from noisy floods entirely.

These take ~5 minutes to configure and pay for themselves the first
time a scanner finds the domain.

### 4. Clone the repo onto the HA host

```
cd /root
git clone https://github.com/tonyuatkins-afk/barelybooting-server.git
cd barelybooting-server
```

(Or `scp` / `rsync` the directory across if you prefer not to put git
on the HA host.)

### 5. Create the `.env`

```
cp .env.example .env
nano .env
```

Paste the tunnel token from step 2 into `CLOUDFLARE_TUNNEL_TOKEN=`.
Set `BAREBOOT_PUBLIC_BASE` to match the hostname you configured in
the tunnel (e.g. `https://barelybooting.com`).

The `.env` stays plaintext on the host filesystem. That is the chosen
risk posture for this project: a single admin, a controlled host, and
a tunnel token that is rotatable via the Cloudflare dashboard if
compromise is suspected. For a slightly harder setup, `chmod 600 .env`
so only the file owner can read it.

### 6. Build and start

```
docker compose build
docker compose up -d
```

First startup:
- `barelybooting` builds the image, runs `init-db` against the volume,
  starts waitress on port 8080 inside the container.
- `cloudflared` waits for `barelybooting` to go healthy (via its
  `/api/v1/health` check), then registers with Cloudflare and opens
  the outbound tunnel.

Check:

```
docker compose logs -f barelybooting
docker compose logs -f cloudflared
```

Healthy cloudflared logs include lines like `Registered tunnel
connection` (usually 4 of them, one per edge region).

### 7. Smoke test

From anywhere:

```
curl https://barelybooting.com/api/v1/health
# {"status":"ok"}
```

Then POST a sample INI:

```
curl -X POST --data-binary @sample.ini \
  -H "Content-Type: text/plain" \
  https://barelybooting.com/api/v1/submit
# a1b2c3d4
# https://barelybooting.com/cerberus/run/a1b2c3d4
```

## Application rate limiting

The app enforces per-IP limits on `POST /api/v1/submit` using
Flask-Limiter:

- 5 requests per minute
- 30 requests per hour

The key is the `CF-Connecting-IP` header Cloudflare injects. Since the
tunnel is the only ingress, the header is trustworthy (nothing else
can reach the container). Requests over the limit get a 429. Tune
these in `barelybooting/routes/api.py`.

`GET /api/v1/health` is exempt so container health checks and any
Cloudflare health probes always succeed.

## Data persistence

SQLite lives on the named Docker volume `barelybooting_data`, mounted
at `/data` inside the container. Survives container restarts and image
rebuilds. Back it up with:

```
docker run --rm -v barelybooting_data:/data -v $PWD:/backup \
  alpine tar czf /backup/barelybooting-$(date +%F).tar.gz -C /data .
```

Schedule that nightly via cron or a Home Assistant automation. A
dead-simple one: a shell command sensor or shell_command service that
runs the tar line, with an automation trigger at 03:00.

## Updating

```
cd /root/barelybooting-server
git pull
docker compose build
docker compose up -d
```

`up -d` recreates the container with the new image; the volume is
untouched, so no data loss.

### Bumping pinned image tags

- Python base: `FROM python:3.12-slim-bookworm` tracks 3.12 patch
  releases. Rebuild monthly to pick up CVE fixes.
- Cloudflared: pinned to a specific release in `docker-compose.yml`.
  Check <https://github.com/cloudflare/cloudflared/releases> and bump
  the tag quarterly.

## Rolling back

`docker compose down` stops both containers but leaves the volume.
To revert to an earlier image, check out the prior git commit and
re-run `build` + `up -d`.

## Alternative: Portainer route

If you would rather not shell into the HA host:

1. Install the Portainer community add-on.
2. Open Portainer's UI.
3. Stacks > Add stack > Web editor.
4. Paste the contents of `docker-compose.yml`.
5. Environment variables > add `CLOUDFLARE_TUNNEL_TOKEN` and
   `BAREBOOT_PUBLIC_BASE`.
6. Upload the build context as a tarball, or point Portainer at the
   GitHub repo URL and let it clone.
7. Deploy.

Same result, no shell required.

## Troubleshooting

- **Tunnel logs show `error="context canceled"` on startup.** Token
  is wrong or the tunnel was deleted on the Cloudflare side. Regenerate.
- **`curl barelybooting.com` returns a Cloudflare 1033 page.** The
  tunnel is up but no public hostname is mapped. Revisit step 2.6.
- **Health endpoint returns 502.** `cloudflared` reached the edge but
  `barelybooting:8080` is unreachable. Check `docker compose ps` and
  container logs; the two services share the `web` network by default.
- **POST returns 413.** Body exceeded 64 KB. Either the client sent a
  truncated / junk body, or `MAX_CONTENT_LENGTH` in
  `barelybooting/__init__.py` was lowered.
- **POST returns 429.** Rate limit hit. Either a real flood or the
  client is retrying faster than the contract expects (the CERBERUS
  contract says one POST per run, no retries). Check
  `docker compose logs barelybooting` for `CF-Connecting-IP`.
- **Uploads succeed but no rows in the browse UI.** Check that the
  volume is actually mounted: `docker compose exec barelybooting ls
  /data` should show `barelybooting.sqlite` and WAL/SHM files.

## Container hardening

The `barelybooting` service is configured with:

- `read_only: true` with `tmpfs: /tmp` (filesystem is immutable except
  for the mounted `/data` volume)
- `cap_drop: [ALL]` (no Linux capabilities)
- `security_opt: [no-new-privileges:true]`
- Runs as UID 1000 (`app`), not root

Same drops apply to `cloudflared`. If you ever need to shell in for
debugging, `docker compose exec` still works; these options restrict
what a compromised process can do, not what the operator can do.

## Rotating secrets

- **Cloudflare Tunnel token:** delete the tunnel in the Zero Trust
  dashboard, create a new one with the same public hostname, update
  `.env`, `docker compose up -d`. The DNS CNAME updates automatically.
- **HA host access:** out of scope for this repo; manage in HA's own
  user settings.
