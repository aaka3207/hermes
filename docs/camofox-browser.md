# Camofox browser (optional companion service)

> **Note (stale):** This file describes a standalone `make up` / host `docker run` recipe. That is
> **not** how camofox was actually deployed — it runs as its own Coolify application built from the
> `aaka3207/camofox-browser` fork. See the "Camofox browser companion" section in the top-level
> `README.md` for the canonical setup.

Anti-detection local browser ([jo-inc/camofox-browser](https://github.com/jo-inc/camofox-browser),
a REST wrapper around [Camoufox](https://camoufox.com) — a Firefox fork that spoofs
fingerprints at the C++ level). Lets the Hermes agent drive a real, stealthy browser and
reuse sessions you've logged into.

**Status:** not deployed. This is a recipe to pull the trigger on later.

## Why it's a *separate* service (not baked into the hermes image)

- It's its own Node.js server, unrelated to the hermes build. Coupling their deploys buys nothing.
- There is **no published registry image**. `make` builds it locally after downloading the
  ~300MB Camoufox binary.
- The default `Dockerfile` bind-mounts a pre-fetched `dist/` (populated by `make fetch`), so a
  plain `docker build`/Coolify build-pack against it fails. (`Dockerfile.ci` self-downloads at
  build time and *would* work in Coolify — only relevant if you ever want it managed there.)

So: **run it standalone on the host.** Keep it out of the hermes Coolify compose.

## Threat model (decided)

Interactive **login is LAN-only** — you only ever open the VNC console from inside your LAN.
The agent reusing saved sessions runs 24/7 with no console exposed. This gates the dangerous
surface (full keyboard/mouse control of a browser logged into your accounts) while leaving the
useful surface (agent reusing cookies) always-on. Nothing about Camofox is exposed to the
internet — no Coolify domain, no port-forward.

## Build

On the host (`192.168.1.100`). Confirm architecture first:

```bash
uname -m          # x86_64 (Intel) → image tag suffix -x86_64 ; aarch64 → -aarch64
git clone https://github.com/jo-inc/camofox-browser && cd camofox-browser
make build        # fetches Camoufox+yt-dlp into dist/, builds camofox-browser:135.0.1-<arch>
```

`make up` exists but its `docker run` is minimal (no VNC port, no volume, no env). We run it
manually instead so persistence + VNC + hardening are wired in.

## Run

```bash
docker run -d --name camofox-browser --restart unless-stopped \
  -p 9377:9377 \                 # REST API (hermes talks here)
  -p 6080:6080 \                 # noVNC web console (LAN-only login)
  -v ~/.camofox:/home/node/.camofox \   # persists profiles/ + cookies/ + traces/
  -e ENABLE_VNC=1 \
  -e VNC_PASSWORD='<set-a-real-password>' \
  -e BROWSER_IDLE_TIMEOUT_MS=300000 \    # kill Firefox after 5 min idle → ~40MB
  -e MAX_OLD_SPACE_SIZE=128 \            # Node heap cap
  -e CAMOFOX_CRASH_REPORT_ENABLED=false \ # opt out of telemetry-to-GitHub-issues
  camofox-browser:135.0.1-x86_64         # match `uname -m`
```

Optional hardening / features (add as `-e`):

| Env var | Purpose | Note |
|---|---|---|
| `CAMOFOX_API_KEY` | Enables cookie-import + `storage_state` export endpoints (403 if unset) | Needed only if you use cookie import / storage export. Set to a random hex string. |
| `CAMOFOX_ACCESS_KEY` | Bearer-token-gates **all** routes except `/health` | Recommended, but **verify hermes can send it** before enabling (see Hermes wiring). On a trusted LAN, `VNC_PASSWORD` is the hard requirement; this is defense-in-depth against other LAN devices. |
| `CAMOFOX_ADMIN_KEY` | Gates `POST /stop` | Optional. |

`VNC_PASSWORD` is **not optional** — without it anyone on your wifi (IoT, guest devices,
roommates) can puppet your logged-in browser. VNC traffic is plaintext, which is fine over LAN
*only* because it never crosses the internet.

## Persistence layout (on the volume)

```
~/.camofox/
├── cookies/                         # bootstrap cookie files you drop in (Netscape format)
└── profiles/<hashed-userId>/
    └── storage_state.json           # auto-saved cookies + localStorage per userId
```

Persistence is **on by default**. It checkpoints on session close and restores on session
create — so "you logged in once" becomes "the agent is authenticated forever after."

## The login → agent-reuse workflow

The link between you and the agent is a shared **`userId`**. Pick one (e.g. `hermes`) and use it
on both sides.

1. **Open the console** (from a LAN device): `http://192.168.1.100:6080/vnc.html`, enter `VNC_PASSWORD`.
2. **Start a session at the login page** (from the host or any LAN box):
   ```bash
   curl -X POST http://192.168.1.100:9377/tabs \
     -H 'Content-Type: application/json' \
     -d '{"userId":"hermes","sessionKey":"login","url":"https://accounts.google.com"}'
   ```
3. **Log in visually** in the VNC window — MFA, CAPTCHAs, OAuth consent, all by hand.
4. Persistence auto-saves `storage_state.json` for userId `hermes` on session close.
   (Optionally export explicitly: `GET /sessions/hermes/storage_state` with the API-key bearer.)
5. **The agent reuses it** — every future session hermes opens under userId `hermes` restores
   that authenticated state automatically. No console needed again until the login expires.

### Alternative: cookie import (no VNC, for simpler sites)

```bash
mkdir -p ~/.camofox/cookies
cp ~/Downloads/linkedin_cookies.txt ~/.camofox/cookies/linkedin.txt   # Netscape format export
# then in Discord: "import my LinkedIn cookies from linkedin.txt"
```
Requires `CAMOFOX_API_KEY` set. The agent calls `camofox_import_cookies`, the server injects
them, and subsequent tabs to that domain are authenticated.

## Hermes wiring

`CAMOFOX_URL` env on the hermes container (LAN IP, since hermes is in its own Coolify stack):

```
CAMOFOX_URL=http://192.168.1.100:9377
```
If you set `CAMOFOX_ACCESS_KEY`, hermes must send it as a bearer token — confirm the Hermes
camofox client supports an access-key/api-key setting before enabling that gate; otherwise leave
it unset and rely on LAN trust + `VNC_PASSWORD`.

`config.yaml` (edit **as the hermes user**, never root — same volume-ownership trap as always):

```yaml
browser:
  camofox:
    managed_persistence: true   # use persistent profiles — the whole point
    user_id: hermes             # MUST equal the userId you VNC-login under
    session_key: default
```

The `user_id` match is the linchpin: it's what makes "I logged in" and "the agent browses
authenticated" the same profile on disk.

## Pre-flight security checklist

- [ ] `VNC_PASSWORD` set to something real
- [ ] Router has **no port-forward / UPnP** rule for `6080` or `9377` (this is what keeps it LAN-only)
- [ ] `~/.camofox` is on a real disk and owned correctly (container runs as `node`)
- [ ] (optional) `CAMOFOX_API_KEY` set if using cookie import / storage export
- [ ] (optional) `CAMOFOX_ACCESS_KEY` set + verified hermes passes it

## Resource profile

| State | RAM |
|---|---|
| Idle (browser not launched) | ~40MB |
| Active browsing | ~200MB browser + ~128MB heap ≈ **~350MB** |

Lazy launch + idle shutdown (`BROWSER_IDLE_TIMEOUT_MS`, default 5 min) means it's ~40MB at rest
and only balloons while a page is actually being driven. Sessions auto-expire after 30 min idle
(`SESSION_TIMEOUT_MS`); the browser process dies after 5 min idle and relaunches on the next
request.

## Lifecycle

```bash
docker stop camofox-browser && docker rm camofox-browser   # stop
docker logs -f camofox-browser                             # logs
# rebuild after a version bump:
cd camofox-browser && make reset
```
Survives host reboots via `--restart unless-stopped`. Profiles persist on the `~/.camofox`
volume across container recreation.
