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

### Apex coexistence via Cloudflare Worker

If the apex `barelybooting.com` is already serving a GitHub Pages
landing site and you want `/api/*` + `/cerberus/*` at the apex
without moving the landing page, use the Worker approach:

1. Add a tunnel public hostname `tunnel.barelybooting.com` with
   service `http://barelybooting:8080`. This gives the Worker an
   internal-ish hostname to proxy through.
2. Leave the apex DNS pointing at GitHub Pages as it already is.
3. Workers & Pages > Create Worker. Paste
   `deploy/cloudflare-worker.js` from this repo into the editor.
4. Add a route: `barelybooting.com/*` triggers the Worker. The
   Worker then fans out: `/api/*` and `/cerberus/*` hit the tunnel
   via `tunnel.barelybooting.com`; everything else is proxied
   through to GitHub Pages.

Free tier ceiling: 100k requests/day. CERBERUS's DOS client keeps
`UPLOAD_URL "http://barelybooting.com/api/v1/submit"` unchanged.

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

Also set **HSTS** for browser traffic: SSL/TLS > Edge Certificates >
HTTP Strict Transport Security (HSTS) > Enable. Suggested settings:
`max-age=6 months`, include subdomains ON, preload OFF (you can
opt into the preload list later once you're sure the domain should
only ever serve HTTPS). HSTS is deliberately NOT set by the app
itself: Cloudflare terminates TLS at the edge, and the origin also
accepts plain HTTP from DOS clients per the upload contract. The edge
injects HSTS for browser viewers without constraining the DOS-client
path.

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

## Threat model

This is a small Flask app accepting public, anonymous uploads from
DOS clients. The project deliberately runs on a co-tenant Home
Assistant OS mini PC. Here is what's mitigated, what isn't, and why.

### Mitigated

- **Remote code execution blast radius.** The container is
  non-root (UID 1000), `read_only: true` with `tmpfs: /tmp`,
  `cap_drop: [ALL]`, `no-new-privileges`, and resource-capped
  (memory 256 MB, CPU 0.5, PIDs 128). An RCE in Flask / Jinja /
  waitress / SQLite inherits none of the capabilities it would
  need to persist on disk, escalate, or exhaust the host.
- **Unauthenticated abuse.** Per-client rate limit on the intake
  endpoint (5/min + 30/hour keyed on `CF-Connecting-IP`). Payload
  size ceiling (64 KB `MAX_CONTENT_LENGTH`, enforced by Werkzeug
  before the parser runs). Strict input validation: ASCII-only
  decode, content-type filter, hex-shape regex on signatures,
  length caps on nickname/notes.
- **Stored-XSS escalation.** CSP response header blocks scripts
  entirely (`script-src 'none'`). `X-Frame-Options: DENY` blocks
  clickjacking. Jinja autoescape covers user fields; no `|safe`.
- **Supply-chain drift on images.** Python base pinned to
  `python:3.12-slim-bookworm`, cloudflared pinned to a specific
  release tag. Rebuild cadence documented above.
- **Public-internet discovery of the host.** Cloudflare Tunnel is
  outbound-only. The UDM Pro has no port forward, no DDNS, no
  inbound rule. Host IP is not discoverable from the public
  `barelybooting.com` hostname.

### Residual risk: container-to-LAN egress

A successful RCE inside the app container cannot write to disk,
gain new capabilities, or exhaust host resources. But Docker's
default NAT still lets the container's outbound network traffic
reach both the internet and, via the host's route table, the
10.69.69.0/24 LAN. An attacker could in principle scan the LAN,
probe Home Assistant's API at `10.69.69.211:8123`, or reach out to
the 486 at `10.69.69.160`.

Docker's `internal: true` flag would block all egress and solve
this, but `cloudflared` needs outbound connectivity to reach the
Cloudflare edge. The clean fix is a dedicated egress-only
namespace for `cloudflared` while the app container is pinned to
an internal network, which is architecturally right but
disproportionate for this project's size. The realistic
mitigations that ARE in place (no-root + read-only FS + dropped
caps + no shell in the image + no package manager at runtime)
make RCE-to-LAN-pivot implausible without also escaping the
container, which the other mitigations make hard.

**Accepted.** If the calculus changes (e.g., the server ever
stores credentials, or HA starts running things that matter more
than home automation), revisit with a split-network topology.

### Residual risk: plaintext transit from DOS clients

Per the upload contract (`docs/ini-upload-contract.md` in the
CERBERUS repo), v0.7.0 clients POST over HTTP, not HTTPS. Vintage
DOS network stacks cannot negotiate modern TLS. Cloudflare's edge
still terminates TLS for modern browser viewers; the unprotected
segment is between the DOS client and Cloudflare's edge on the
open internet.

**Accepted** because the payload is explicitly public data (the
whole purpose is to publish it on the browse UI) and there is no
auth token in the request. An on-path attacker could replay or
manipulate a POST, but replay only lands a duplicate submission
(caught by the `run_signature` UNIQUE constraint, 409'd), and
manipulation lands garbage that the input validation rejects.
TLS for DOS clients is reserved for a future CERBERUS v0.8.0+
via the NetISA card's hardware TLS path.

### Out of scope

- **DDoS beyond the edge.** Cloudflare's free plan absorbs
  volumetric attacks; custom WAF rules (documented in "Enable
  Cloudflare's edge protections" above) handle targeted floods.
  Beyond that is a Cloudflare billing question, not a code one.
- **Account / auth compromise.** The server has no accounts.
  Anyone can submit. The HA host's own auth is a separate
  question, managed in HA's user settings.
- **Physical access to the HA mini PC.** If someone has physical
  access, they own the volume, the `.env` file, and the tunnel
  token. That is a perimeter concern, not an app concern.

## Rotating secrets

- **Cloudflare Tunnel token:** delete the tunnel in the Zero Trust
  dashboard, create a new one with the same public hostname, update
  `.env`, `docker compose up -d`. The DNS CNAME updates automatically.
- **HA host access:** out of scope for this repo; manage in HA's own
  user settings.
