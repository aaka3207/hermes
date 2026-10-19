# Tailscale private access: prep (2026-10-19)

Goal: make Hermes, OpenViking, Coolify and other admin surfaces reachable only from the tailnet, while keeping their `*.aakashe.org` names. MetaMCP stays public and becomes the single MCP gateway for both Hermes and Claude Desktop.

This is a prep doc: inventory, design, open questions and a phased plan. Nothing here has been applied.

## 1. Current state (read-only inventory, 2026-10-19)

### Host

| Item | Value |
|---|---|
| OS | Ubuntu 24.04.3, kernel 6.8.0-134, iptables 1.8.10 (nf_tables backend) |
| Network | `wlp2s0` 192.168.1.100/24 (Wi-Fi). Also has **global IPv6** `2600:1700:67d0:ea50::/64` (AT&T) |
| Firewall | UFW installed, status unknown (needs sudo); no `DOCKER-USER` rules known |
| Docker | 29.6.1, `default-address-pools: 10.0.0.0/8` (no overlap with Tailscale's 100.64.0.0/10) |
| Tailscale | Not installed on the server. The Mac has Tailscale.app, logged out |
| sudo | Needs a password over SSH, so the host steps are run by the user |

### Coolify and proxy

- Coolify 4.4.6, single server `localhost` (Coolify reaches it over SSH to `host.docker.internal`; unaffected by this change).
- Traefik 3.6.25 (`coolify-proxy`), ports `80`, `443`, `443/udp`, `8080` on `0.0.0.0`. The ACME resolver `letsencrypt` uses the **HTTP challenge**.
- **The proxy has saved but unapplied settings** (`last_saved_settings` ≠ `last_applied_settings`). Diffed 2026-10-19: the running container matches `/data/coolify/proxy/docker-compose.yml` (written 2026-08-09). The saved config differs only in labels. It sets `traefik.enable=false` and drops the three `traefik.http.routers.traefik.*` / `services.traefik.*` labels. Command args, ports and volumes are identical.
  - What those labels do today: they expose the Traefik dashboard API (`api@internal`) on port 80 under the default rule `Host(`traefik-coolify-proxy`)`. `curl -H 'Host: traefik-coolify-proxy' http://127.0.0.1/api/overview` returns 200. That is read-only, but it lists every router, hostname and middleware to anyone on the LAN (and on IPv6, if that is open). The tunnel can't reach it, because cloudflared forwards the real `*.aakashe.org` Host header.
  - Verdict: the pending change is a hardening. Let it apply with the Phase 2 restart.
  - Separately, `8080:8080` is published but nothing listens there (`api.insecure=false`, no 8080 entrypoint). Drop it in Phase 2 or Phase 4.
- Dynamic config `/traefik/dynamic/coolify.yaml` routes `coolify.aakashe.org` (`/app`, `/apps` → realtime :6001, `/terminal/ws` → :6002).

### Cloudflare Tunnel (remotely managed, cloudflared runs with `network_mode: host`)

| # | Hostname | Service |
|---|---|---|
| 1 | `coolify.aakashe.org` path `/terminal/ws` | `http://localhost:6002` |
| 0 | `coolify.aakashe.org` | `http://localhost:8000` (straight to Coolify, bypasses Traefik) |
| 2 | `netdata.aakashe.org` | `http://localhost:19999` (netdata is **not running**, 502) |
| 3 | `*.aakashe.org` | `http://localhost:80` (Traefik, `http` entrypoint) |
| – | catch-all | `http_status:404` |

So every Coolify app with a domain is public today. Coolify domains are all `http://...` because Cloudflare terminates TLS.

### Routed hostnames (Traefik labels on running containers)

| Hostname | Resource | Plan |
|---|---|---|
| `hermes-dashboard`, `hermes-api`, `hermes-webhooks` | hermes app (`tgg4k0sc8wgocck08cc4s4cg`) | private; drop `hermes-webhooks` (webhook platform has no routes) |
| `hermes-webui` | hermes app | private |
| `mnemosyne` | hermes app (`mnemosyne-dashboard`) | being removed |
| `syncthing-hermes`, `syncthing-gateway` | hermes app (`syncthing`) | being removed |
| `ov` | openviking service (`waw5ghezsp84k9ytyqcnsqi1`, `http://ov.aakashe.org:1933`) | private |
| `metamcp` | metamcp service (`vkokwgggswokogg484oww0gc`) | **public** |
| `windmill` | windmill service | private (Hermes calls `windmill.aakashe.org/api/mcp`) |
| `cognee`, `cognee-ui` | cognee service | being removed |
| `mnemosyne-mcp` | mnemosyne-mcp app | being removed |
| `gbrain-life` | gbrain-life app | being removed |
| `paperclip` | paperclip service | being removed |
| `k8oc08...sslip.io` | monarch-mcp app | MetaMCP calls it internally (`http://monarch-mcp:9000`), so the sslip name can go |

Not served by this Traefik:
- `homeassistant.aakashe.org` answers 200 from another origin, probably its own tunnel or add-on. Out of scope; stays public.
- `n8n.aakashe.org` returns 404 and the n8n service has exited.

### Host-published ports (LAN exposure, bypasses Traefik)

Port 22, plus:

| Port | Service |
|---|---|
| 80, 443, 8080 | Traefik |
| 8000, 6001, 6002 | Coolify |
| 8642, 9119 | Hermes |
| 8787 | Hermes WebUI |
| 8384, 22000 | Syncthing |
| 9000 | monarch-mcp |
| 8095, 8096 | gbrain and another app |
| 6080, 9377 | camofox |
| 20241 | cloudflared metrics |

All are on `0.0.0.0` and `[::]`. Tailscale does not change any of this. It is phase 4.

### Who calls the names that go private

| Caller | Target today | After |
|---|---|---|
| Hermes OpenViking plugin (`/opt/data/.openviking/ovcli.conf.owner`) | `https://ov.aakashe.org` | Works unchanged if containers are allowed through (see 2.4) |
| MetaMCP `openviking` server | `https://ov.aakashe.org/mcp` | Same. **The server is not mapped to any namespace today** |
| claude.ai / Desktop OpenViking connector | `ov.aakashe.org` OAuth | Breaks; replaced by a MetaMCP endpoint (phase 3) |
| Hermes config/skills | `windmill`, `coolify`, `hermes-dashboard`, `homeassistant`, `metamcp`, `gbrain-life` | Same container rule; `gbrain-life` refs to clean up |
| Hermes MCP OAuth redirect (`dashboard.public_url`) | `https://hermes-dashboard.aakashe.org` | Name unchanged, so redirects stay valid |
| Photon (iMessage) | outbound stream | unaffected |
| GitHub → Coolify webhooks | `coolify.aakashe.org/webhooks/...` | Would break. hermes shows auto-deploy **on** in Coolify, but deploys are manual in practice. Confirm nothing relies on it |

MetaMCP namespaces today:
- `hermes` endpoint → `hermes` namespace: hevy-mcp, Monarch
- `ameer` endpoint → `Personal` namespace: Monarch, home-assistant (off)
- Other endpoints are per-tool: monarch, Akiflow, hevy, HA, mnemosyne, cognee, openclaw, Myfitnesspal

## 2. Design

### 2.1 Split by Traefik entrypoint

The tunnel already reaches Traefik on `:80` (`http` entrypoint) only. Private names go direct over the tailnet to `:443` (`https` entrypoint). So:

- **`https` entrypoint gets an `ipAllowList` middleware** at entrypoint level (`--entrypoints.https.http.middlewares=tailnet-only@file`). Every router on 443 is tailnet-only by default, with no per-app labels. That matters, because Coolify regenerates labels and compose router names carry uuids.
- **`http` entrypoint stays as is** for tunnel traffic. Private apps get `https://` domains in Coolify. Their http routers become redirects to https, which the allowlist then blocks for non-tailnet clients.
- To verify in a test: entrypoint-level middleware syntax on Traefik 3.6, and whether Coolify's proxy UI keeps the extra flag across "Reset". It is a static flag in the proxy compose.

### 2.2 DNS: same names, grey-cloud A records to the tailnet IP

- For each private name, add an **explicit DNS-only (grey) A record** `name.aakashe.org → 100.x.y.z` (the server's Tailscale IP). Per Cloudflare's docs, an exact record beats the `*` wildcard.
- Resolution works everywhere: phone, laptop, with MagicDNS off, with browser DoH. Only tailnet devices can connect; others time out. No dnsmasq or split DNS is needed.
- The trade-off is that public DNS reveals the 100.x address and the service names. That is low risk.

### 2.3 TLS: DNS-01 via Cloudflare, one wildcard cert

- Switch the existing `letsencrypt` resolver from the HTTP challenge to `dnschallenge.provider=cloudflare`. Coolify's documented method keeps the resolver name, so every generated label still works.
- Credentials: `CF_DNS_API_TOKEN` scoped to the `aakashe.org` zone, with **Zone:Zone:Read + Zone:DNS:Edit**.
- Add a dynamic-config router that requests `*.aakashe.org`, using Coolify's wildcard-certs recipe. One cert covers all private names, keeps them out of Certificate Transparency logs and avoids rate limits.
- Traefik 3.6 option names: `propagation.delayBeforeChecks`, not the deprecated `delaybeforecheck`.
- Test against the Let's Encrypt staging CA first.

### 2.4 Allowlist ranges

```yaml
http:
  middlewares:
    tailnet-only:
      ipAllowList:
        sourceRange:
          - "100.64.0.0/10"        # tailnet IPv4
          - "fd7a:115c:a1e0::/48"  # tailnet IPv6 (verify)
          - "10.0.0.0/8"           # this host's Docker networks (containers calling private names)
```

- **Why the Docker range:** a container that resolves `ov.aakashe.org` connects to the host's own 100.x address. Docker DNATs that to Traefik with the container's 10.x source. Allowing 10.0.0.0/8 lets Hermes and MetaMCP keep using `https://ov.aakashe.org` with no config change.
- **Why this is safe:** tunnel traffic arrives on the `http` entrypoint, not `https`, so it never meets this list. LAN clients (192.168.1.x) are not in it.
- **To verify:** from a tailnet device, Traefik sees a 100.x source, not a Docker gateway IP.
- Alternative if this proves leaky: point the plugin and MetaMCP at internal Docker names (`http://openviking-…:1933`) via "Connect to Predefined Network" and drop 10/8.

### 2.5 Tailscale on the host

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --hostname=homeserver --advertise-tags=tag:server --ssh --accept-dns=false
tailscale ip -4
```

- `--accept-dns=false` keeps the host resolver, and so Docker's, as it is.
- A tagged node gets key expiry disabled automatically.
- Policy (admin console → Access controls). Replace the default allow-all:

  ```json
  "tagOwners": { "tag:server": ["autogroup:admin"] },
  "grants": [
    { "src": ["autogroup:member"], "dst": ["tag:server"], "ip": ["80", "443", "8000"] }
  ],
  "ssh": [
    { "action": "check", "src": ["autogroup:member"], "dst": ["tag:server"], "users": ["ameer", "root"] }
  ]
  ```

  `8000` is the Coolify fallback over the tailnet IP while the domain is being moved.
- Clients: Mac (log back in), phone, and any other device that should reach the private names.
- **Bonus:** the SSH MCP profile can move from `192.168.1.100` to the tailnet name and stop failing off the home network.

### 2.6 MetaMCP as the gateway

- Point the `openviking` server at `https://ov.aakashe.org/mcp`, or an internal name. Auth is the `hermes` OpenViking key as a header.
- Map it into:
  - the `hermes` namespace (Hermes keeps its plugin for recall; the MCP tools become optional)
  - a namespace Claude Desktop uses (`Personal` behind the `ameer` endpoint, or a new `openviking` endpoint with OAuth for claude.ai)
- Key: resolved. Both namespaces share MetaMCP's one `openviking` server, so Desktop and Hermes reach OpenViking with whatever key that server holds, and see the same memories. Hermes's own plugin already uses the owner key. If Desktop ever needs a separate identity, add a second MetaMCP server entry with the other key.
- Remove the direct claude.ai OpenViking connector once the MetaMCP route works. OpenViking's own OAuth (`oauth.enabled`, `OPENVIKING_PUBLIC_BASE_URL`) can then stay or be turned off.

### 2.7 Coolify dashboard

- Remove tunnel routes 0 and 1.
- Add a grey A record `coolify.aakashe.org → 100.x`.
- Change `/traefik/dynamic/coolify.yaml` routers to the `https` entrypoint with `tls.certResolver: letsencrypt`. That brings in the allowlist and realtime/terminal over 443.
- Fallbacks: `http://192.168.1.100:8000` on the LAN and `http://100.x:8000` over the tailnet. Keep port 8000 published until the domain route is verified.
- Coolify **Settings → Advanced → DNS Validation** will flag 100.x records. Turn it off, or confirm it only warns.

## 3. Phased plan

Each phase is reversible on its own. Gated steps (Coolify proxy restart, deploys, Cloudflare and host changes) are run by the user.

**Phase 0: cleanup (separate task, before or after)**
- Remove cognee, mnemosyne-mcp, gbrain-life and paperclip in Coolify after exporting their volumes.
- Repo change: drop `syncthing`, `mnemosyne-dashboard` and the mnemosyne install from the Hermes compose and Dockerfile.
- Clean `gbrain-life` and `mnemosyne` refs from Hermes config and skills (Hermes-maintained).
- Fewer names to move.

**Phase 1: Tailscale up, nothing private yet**
1. Install Tailscale on the server (2.5); log in on the Mac and phone.
2. Apply the policy; verify `tailscale ping homeserver` and `curl -I http://100.x:8000` from the Mac.

**Phase 2: proxy changes (one proxy restart, a few seconds of outage for all domains)**
1. Pending proxy settings: diffed (section 1). They only remove the dashboard router; accept them.
2. Create the Cloudflare API token.
3. Edit the proxy config:
   - `CF_DNS_API_TOKEN` env
   - swap the HTTP challenge for `dnschallenge.provider=cloudflare` plus resolvers `1.1.1.1:53,1.0.0.1:53`
   - staging `caServer` for the first run
   - `--entrypoints.https.http.middlewares=tailnet-only@file`
4. Add dynamic configs `tailnet-only.yaml` (2.4) and `wildcard-cert.yaml` (2.3).
5. Restart the proxy. Check the logs for the issued staging wildcard cert, then remove `caServer` and restart again.
6. Verify public names via the tunnel still work (`metamcp`, `homeassistant`).

**Phase 3: move names one at a time (OpenViking first, as the pilot)**

For each name:
1. In Coolify, change the domain from `http://name.aakashe.org:PORT` to `https://name.aakashe.org:PORT` and redeploy or restart.
2. In Cloudflare, add the grey A record `name → 100.x`.
3. Verify:
   - on the tailnet: browser and `curl` return 200 with a valid cert
   - off the tailnet (phone on cellular, Tailscale off): times out
   - LAN without Tailscale: `curl -H 'Host: name.aakashe.org' https://192.168.1.100 -k` returns 403
   - from inside the hermes container: `curl https://ov.aakashe.org/health` returns 200

Order:
- `ov`: then do the MetaMCP mapping (2.6) and a Hermes recall test.
- `windmill`
- `hermes-dashboard`: re-test one OAuth MCP server (Akiflow).
- `hermes-webui`, `hermes-api`: drop the `hermes-webhooks` domain.
- `coolify` (2.7)
- Finally, in the tunnel: replace `*.aakashe.org` with an explicit `metamcp.aakashe.org → http://localhost:80` and delete the `netdata` route. Leave HA's origin as it is.

Rollback per name: delete the A record (the wildcard tunnel takes over again) and set the domain back to `http://`.

**Phase 4: LAN and IPv6 hardening (later)**
- Bind Hermes's 8642/9119/8787 and other published ports to `127.0.0.1`, or remove them (repo compose change).
- Close Coolify 8000/6001/6002 via `docker-compose.custom.yml` once the domain route is proven.
- Decide on UFW: `allow in on tailscale0`, LAN SSH, plus `DOCKER-USER` rules.

## 4. Open questions

Answered 2026-10-19:
- **Router port forwards:** none that you know of. The server has a static LAN IP (192.168.1.100). This matches the hairpin test, which timed out.
- **AT&T IPv6 inbound:** you believe it's blocked (the BGW default). Not verified. The server does have public IPv6 addresses (`2600:1700:67d0:ea50::45`, plus a SLAAC `/64` address), so a 2-minute check is still worth doing. From a phone on cellular with Wi-Fi off, run `curl -m5 http://[2600:1700:67d0:ea50::45]/` or use an IPv6 port checker on 80, 443, 8000 and 8642. A timeout means it's closed. Phase 4 stays "later" either way.
- **Claude Desktop → OpenViking:** through MetaMCP, which has its own auth. Desktop never holds an OpenViking key; the key that MetaMCP's `openviking` server already uses stays as it is. Step 2.6 maps that server into the endpoint Desktop uses.
- **Pending proxy settings:** diffed (section 1). They remove the Traefik dashboard router; accept them.

Still open:
1. Is anything relying on GitHub → Coolify auto-deploy webhooks? These die once `coolify.aakashe.org` goes tailnet-only.
2. Which devices join the tailnet: Mac, iPhone, anything else (Conduit on the phone uses the tailnet name)?

## Sources

- Tailscale docs via ctx7 `/websites/tailscale`: `tailscale up` flags, key expiry, grants, firewall mode, secure-server-ubuntu.
- Traefik via ctx7 `/traefik/traefik`: ACME DNS challenge, v3.3 propagation option rename, `ipAllowList`.
- lego Cloudflare provider (go-acme.github.io/lego/dns/cloudflare/): token permissions.
- Coolify via ctx7 `/coollabsio/coolify-docs`: Traefik DNS challenge, wildcard certs, proxy config, labels modes, predefined network, DNS validation, firewall.
- Cloudflare via ctx7 `/cloudflare/cloudflare-docs`: tunnel ingress matching, wildcard DNS precedence.
- Inventory: Coolify API, `docker inspect`, cloudflared logs, the MetaMCP database (read-only) and Hermes config on 2026-10-19.
