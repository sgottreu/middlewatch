# Deploying the review server

How `review.middlewatch.co` came to exist. [`aws.md`](aws.md) owns storage and
compute — the stories volume, the S3 bucket, the IAM role, ffmpeg — and opens
section 7 with "Open `review.middlewatch.co`" without ever saying how. This page
is that missing half: DNS, TLS, Google sign-in, nginx, and the SSH bootstrap.

**Read the status column before you trust a section.** This was ported from the
session that first stood the box up, on 2026-09-11, and it was deliberately
*not* re-verified against the code as it stands today. Every heading carries the
date it was last checked and what that check was worth. Two months of pipeline
work has landed since, and §5 and §6 are known to have drifted — see
[Known stale](#known-stale).

| § | What | Last checked | Worth |
|---|---|---|---|
| 0 | What the tool is, and why it needs a gate | 2026-09-11 | read from `server.py` |
| 1 | Instance and static IP | 2026-09-11 | console steps, untested |
| 2 | DNS at Hover | 2026-09-11 | `dig` confirmed Hover authoritative |
| 3 | SSH and the Lightsail key trap | 2026-09-11 | documented behaviour, untested |
| 4 | Clone, venv, the two untracked files | 2026-09-11 | **tested** — clean venv, server booted |
| 5 | systemd units | 2026-09-11 | `systemd-analyze verify` clean; **see Known stale** |
| 6 | The story commit timer | 2026-09-11 | **tested** four ways; **see Known stale** |
| 7 | Verifying before exposure | 2026-09-11 | **tested** — ran with every key unset |
| 8 | TLS | 2026-09-11 | standard certbot, untested |
| 9 | Google OAuth client | 2026-09-11 | console steps, untested |
| 10 | oauth2-proxy | 2026-09-11 | config from upstream docs, untested |
| 11 | nginx | 2026-09-11 | untested |
| 12 | Retiring the Bitnami box | 2026-09-11 | from the AWS migration guide |

---

## 0. What the tool is, and why it needs a gate

*2026-09-11 — read from `review/server.py`.*

Three facts about `cli review` shaped every decision below.

**It has no auth.** No login, no sessions, no CSRF token. The module docstring
says a login screen would be "work spent on problems that do not exist" — true
on localhost, and false the moment the thing has a public name.

**It spends money.** `/api/write`, `/api/revise` and `/api/critique` are live
Anthropic calls. An open URL is a stranger with the API key.

**It deletes.** `/api/reject` is `shutil.rmtree` on a bundle. It refuses
anything whose parent is not `stories_dir`, so `../..` is handled — but not
whoever found the URL.

It already binds `127.0.0.1`, which is correct. Keep it that way and put nginx
and oauth2-proxy in front. Nothing in `server.py` needs to change to deploy it.

## 1. The instance and the static IP

*2026-09-11 — console steps, never re-checked.*

Ubuntu 24.04 LTS under **OS Only**, not a WordPress or LAMP blueprint. The 1 GB
bundle is enough: one Python process making HTTPS calls, not a renderer. Take
2 GB if `record` or `design` will also run here — which, as of §Known stale,
they now do.

The public IP Lightsail hands out changes on every stop, so attach a static one.
Ours is **`34.193.44.80`**, a us-east-1 address. Free while attached, billed if
you detach and keep it.

Firewall, on the instance's **Networking** tab: add **HTTPS / TCP 443**. Port 80
is already open and certbot needs it. Restrict **SSH / TCP 22** to your own IP —
it defaults to the whole internet.

## 2. DNS at Hover

*2026-09-11 — `dig` confirmed Hover is authoritative.*

    dig +short NS middlewatch.co

returns Hover's nameservers. AWS is **not** in the DNS path, despite there being
Route 53 zones on the account — those belong to another domain. Do not let the
Lightsail console create a DNS zone for `middlewatch.co` when it offers: a zone
nothing delegates to accepts records and answers nobody, and you find that out
months later while debugging something unrelated.

**hover.com → Domains → middlewatch.co → DNS → Add A Record**, hostname
`review`, value `34.193.44.80`. Leave Hover's default `@`, `www` and mail
records alone — this adds a hostname, it does not replace a zone. The apex is a
separate decision for whenever the landing page ships.

Keeping review on its own subdomain is also what lets the PWA manifest's
`start_url: "/"` work and keeps the OAuth cookie off the public site.

Wait for it before touching certbot — a failed HTTP challenge counts against
Let's Encrypt's rate limit:

    dig +short A review.middlewatch.co     # want 34.193.44.80

## 3. SSH, and the Lightsail key trap

*2026-09-11 — documented Lightsail behaviour, not re-tested.*

**The console's "Upload new key pair" button will not help you.** Imported keys
are only injected into instances created *after* the import, so nothing you
upload reaches an instance that already exists. Bootstrap with Lightsail's own
generated key, then append yours by hand.

Download `LightsailDefaultKey-us-east-1.pem` from **Account → SSH keys**, then:

```bash
mkdir -p ~/.ssh
mv ~/Downloads/LightsailDefaultKey-us-east-1.pem ~/.ssh/
chmod 400 ~/.ssh/LightsailDefaultKey-us-east-1.pem

cat ~/.ssh/id_ed25519.pub | ssh -i ~/.ssh/LightsailDefaultKey-us-east-1.pem ubuntu@34.193.44.80 \
  'mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'
```

A pipe rather than `ssh-copy-id`, which does not ship with macOS. Retyping a
public key by hand is how you get locked out.

Then give it a name, because every command in this repo's docs assumes one:

```
Host middlewatch
    HostName 34.193.44.80
    User ubuntu
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
```

Pinned to the IP rather than the hostname so it works before DNS and keeps
working if the record changes. Once `ssh middlewatch` works on your own key the
`.pem` is only a spare; it stays valid either way.

**Base packages:**

```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install nginx git python3-venv python3-pip certbot python3-certbot-nginx
sudo timedatectl set-timezone America/Chicago
```

## 4. The clone, the venv, and the two untracked files

*2026-09-11 — **tested.** Built a clean venv with exactly the four packages
below, imported `story_pipeline.cli`, booted the server against the real
bundles. No missing modules.*

The deploy key needs **write access**, unlike most: approvals made here exist
nowhere else, and pushing them is both the sync back to the laptop and the only
backup of review state. A deploy key is scoped to one repo, which is the
narrowest write credential GitHub offers.

```bash
ssh-keygen -t ed25519 -C middlewatch-lightsail -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub        # → repo Settings → Deploy keys, "Allow write access"

sudo mkdir -p /srv/middlewatch
sudo chown ubuntu:ubuntu /srv/middlewatch
git clone git@github.com:sgottreu/middlewatch.git /srv/middlewatch

cd /srv/middlewatch
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install anthropic google-genai requests PyYAML
```

**The path must be `/srv/middlewatch`.** `mw-review.service` hardcodes it in
both `WorkingDirectory` and `ExecStart`. Cloning elsewhere and overriding
`MW_REPO` fixes the scripts and leaves the unit broken.

Four packages, not `requirements.txt`: that additionally pulls numpy and scipy
for `ambience_pipeline`, which this box does not run and which are the one thing
likely to exhaust 1 GB of RAM. `boto3` is imported lazily inside the Polly
branch and `tts_provider` is `elevenlabs`, so it is never reached.

> Verified 2026-09-11 only. §Known stale notes that `record` and `design` now
> run here, which means ffmpeg and possibly more packages. Check before
> rebuilding from this list.

`stories/` arrives with the clone — it is tracked. `config.yaml` and `.env` are
not, and without them the server starts happily and then fails on the first paid
call with the SDK's own "could not resolve authentication method", which names
nothing you can act on. From the laptop:

```bash
rsync -av config.yaml .env middlewatch:/srv/middlewatch/
```

Then on the box, lock the keys down and pin the ledger so it cannot follow the
working directory around:

```bash
chmod 600 /srv/middlewatch/.env
echo 'MW_LEDGER_DIR=/srv/middlewatch/ledger' >> /srv/middlewatch/.env
```

`ledger/ledger.py` reads `MW_LEDGER_DIR` with a default of the relative
`ledger`, which is the footgun `CLAUDE.md` warns about under "run everything
from the repo root".

## 5. systemd

*2026-09-11 — `systemd-analyze verify` parsed all three units cleanly.*
**Partly stale, see below.**

The units and scripts live in `scripts/deploy/`, versioned, so they arrive with
a `git pull`:

```bash
cd /srv/middlewatch/scripts/deploy
sudo install -m 755 mw-deploy mw-commit-stories mw-write-running /usr/local/bin/
sudo install -m 644 mw-review.service mw-stories.service mw-stories.timer /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now mw-review
systemctl status mw-review --no-pager
```

`WorkingDirectory` is load-bearing, not tidiness: `main()` calls
`cfgmod.load_env()` before it dispatches and `config.load()` falls back to
`Path("config.yaml")`, so that one line wires up the credentials, the config and
`stories/` at once. That is also why there is no `EnvironmentFile` — the app
reads its own `.env`.

**Never add `--reload`.** It calls `os.execv` to restart the process in place,
which fights the supervisor. It exists for editing code on the laptop; here,
`systemctl restart` is the reload.

## 6. The story commit timer

*2026-09-11 — **tested** four ways: idle server, no server, a stubbed server
reporting a running job, and an already-clean tree.* **The guard has since been
rewritten — see Known stale.**

```bash
sudo systemctl enable --now mw-stories.timer
sudo systemctl start mw-stories        # one pass now, to prove the key can push
journalctl -u mw-stories -n 20 --no-pager
```

`mw-commit-stories` commits nothing when nothing changed, and skips entirely
while a job is in flight — the writer lands chapters one at a time over several
minutes, and a commit taken mid-run captures a bundle in a state no stage meant
anyone to see.

`mw-write-running` asks the *process*, not the disk. `stages.write` in the
manifest is set on entry and only cleared on success, so a hard kill leaves it
reading `running` for good and a guard built on it would block commits until
someone noticed. Silence means idle; a server that is not answering reports
idle, which is correct.

It is deliberately not clever: no `--rebase`, no `--force`. If the laptop and
the box diverge, the commit still lands and only the push fails, turning the
unit red in `systemctl list-timers`. Silently rewriting approval history would
be worse than a red unit.

## 7. Verifying before exposure

*2026-09-11 — **tested.** Ran the full read-only surface with every API key
unset.*

Do not expose it to test it, not even briefly. Forward the port instead:

    ssh -N -L 8765:127.0.0.1:8765 middlewatch

**A working queue proves nothing about credentials.** With every key unset, the
page, `/api/queue`, `/api/story/<slug>`, the icons and the manifest all return
200 and render correctly. Nothing fails until Write or Revise. Check directly —
free, no call made:

```bash
cd /srv/middlewatch && .venv/bin/python3 -c "
from story_pipeline import config
config.load_env()
import os
for k in ('ANTHROPIC_API_KEY','GEMINI_API_KEY','ELEVENLABS_API_KEY'):
    v = os.environ.get(k)
    print(f'{k:22} {\"set, \" + str(len(v)) + \" chars\" if v else \"MISSING\"}')
"
```

As of 2026-09-11 only `ANTHROPIC_API_KEY` was needed here; Gemini and
ElevenLabs belonged to `design` and `record` on the laptop. That has changed —
see Known stale.

> Write-cost figures rendering as `$0` was expected on 2026-09-11: the ledger
> gap in `CLAUDE.md`. `estimate_write` returned `low: 0.0, high: 0.0` because
> `costs.csv` did not exist. Recheck rather than assume.

## 8. TLS

*2026-09-11 — standard certbot, untested.*

A placeholder site first, so certbot has a server block to rewrite. It
deliberately proxies nothing yet.

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name review.middlewatch.co;
    root /var/www/html;
    location / { return 503; }
}
```

```bash
sudo ln -sf /etc/nginx/sites-available/review /etc/nginx/sites-enabled/review
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

sudo certbot --nginx -d review.middlewatch.co --redirect \
     -m sgottreu@gmail.com --agree-tos --no-eff-email
```

Certbot writes the 443 block and the redirect into that file and installs a
renewal timer. Confirm with `sudo systemctl list-timers | grep certbot`.

## 9. The Google OAuth client

*2026-09-11 — console steps, untested.*

New project. **APIs & Services → OAuth consent screen**, user type **External**.
Add no scopes: the defaults — `openid`, `email`, `profile` — are non-sensitive
and are all oauth2-proxy asks for.

**Publish the app.** This matters. An app left in Testing hands out refresh
tokens that expire after seven days, so you re-authenticate constantly.
Publishing one that requests only these scopes needs no Google review.

**Credentials → Create credentials → OAuth client ID → Web application:**

| Field | Value |
|---|---|
| JavaScript origin | `https://review.middlewatch.co` |
| Redirect URI | `https://review.middlewatch.co/oauth2/callback` |

The redirect path must match exactly. A trailing slash is a mismatch.

## 10. oauth2-proxy

*2026-09-11 — config from upstream docs, untested. Pinned v7.15.4, current at
the time.*

It runs as a reverse proxy in front of the review server rather than as an nginx
`auth_request` subrequest: one process fewer to reason about, and it forwards
the whole request itself.

```bash
cd /tmp
ARCH=$(dpkg --print-architecture)
VER=7.15.4
curl -fsSL -O "https://github.com/oauth2-proxy/oauth2-proxy/releases/download/v${VER}/oauth2-proxy-v${VER}.linux-${ARCH}.tar.gz"
tar -xzf oauth2-proxy-v${VER}.linux-${ARCH}.tar.gz
sudo install -m 755 oauth2-proxy-v${VER}.linux-${ARCH}/oauth2-proxy /usr/local/bin/

sudo useradd --system --no-create-home --shell /usr/sbin/nologin oauth2-proxy
sudo mkdir -p /etc/oauth2-proxy
echo 'sgottreu@gmail.com' | sudo tee /etc/oauth2-proxy/emails > /dev/null

python3 -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

`/etc/oauth2-proxy/oauth2-proxy.cfg`, mode 640, owned `root:oauth2-proxy`:

```ini
provider = "google"
client_id     = "..."
client_secret = "..."
cookie_secret = "..."

http_address = "127.0.0.1:4180"
redirect_url = "https://review.middlewatch.co/oauth2/callback"
upstreams    = ["http://127.0.0.1:8765/"]

# nginx terminates TLS, so trust its X-Forwarded-* headers
reverse_proxy = true

# any domain, but only the addresses in this file
email_domains             = ["*"]
authenticated_emails_file = "/etc/oauth2-proxy/emails"

# a month between logins, refreshed hourly — this is a phone app in practice.
# SameSite=lax is load-bearing: the review API is unauthenticated JSON POSTs,
# and lax is what stops another site posting one on your behalf.
cookie_secure   = true
cookie_expire   = "720h"
cookie_refresh  = "1h"
cookie_samesite = "lax"

skip_provider_button = true
```

Adding a reviewer later is one line in `/etc/oauth2-proxy/emails` plus a
restart. No Google Cloud change: a published app accepts any Google account, and
the allowlist is what actually decides.

## 11. nginx

*2026-09-11 — untested.*

Replace `location / { return 503; }` inside the **443** block certbot created:

```nginx
location / {
    proxy_pass http://127.0.0.1:4180;

    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # a revise or a critique was one model call held open in a single request.
    # nginx's 60s default would 504 mid-call — and you would still be billed
    # for the reply it threw away.
    proxy_read_timeout 900s;
    proxy_send_timeout 900s;
    proxy_buffering    off;
}
```

The timeout mattered because paid stages ran synchronously inside the request.
`runner.py` has since moved them to child processes, so the 900s may now be
protecting nothing — harmless either way, but it is no longer the reason.

## 12. Retiring the old Bitnami instance

*2026-09-11 — from AWS's Bitnami migration guide.*

That guide only offers a blueprint-to-blueprint hop for WordPress, LAMP, Nginx
and Node. For anything else it says to start from a base OS blueprint and copy
the data across — which is what this page is. The only thing worth carrying over
is the static IP: detach it there, attach it here, and DNS never changes.

Once the new box has served a real review session over HTTPS: take a **manual
snapshot** of the old instance, confirm no static IP is still attached, then
delete it. Billing stops at deletion, not at stop. Keep the snapshot a month —
the thing you discover you needed from an old box is never the thing you checked
for.

---

## Known stale

Everything below was true on 2026-09-11 and is not true now. Dates are when the
superseding work landed.

**Jobs are processes, not threads.** *(2026-10-20, `review/runner.py`.)* Paid
stages ran as threads inside the server, with job state in a module-level
`_jobs` dict. They now run as children in their own process groups, with state
on disk under `runs/<slug>/<job_id>/` — `meta.json`, `output.log`, `note.txt`.
Per [`review-ui.md`](review-ui.md), `mw-deploy` restarting the server on any
Python change was one of three reasons for the move. So: a restart no longer
loses a running job, and §6's reasoning about in-memory state describes the old
design. The *conclusion* still holds — do not deploy through a running job — but
for a different reason.

**`mw-write-running` reports more than writes.** It now distinguishes `redraft`
(one chapter back with a note), `record` and `design`, printing what each is
rather than assuming chapters. §6's test results covered writes only.

**`scripts/deploy/` has grown.** Added since: `mw-restart`, `mw-ship`,
`mw-s3-archive` with its own service and timer. §5's `install` lines name only
the original six and will leave the rest uninstalled.

**`record` and `design` run on this box.** §4's four-package list and §7's
claim that only `ANTHROPIC_API_KEY` is needed both predate that. ffmpeg is now
required here; see [`aws.md`](aws.md) §4.

**The page is much larger.** `server.py` roughly doubled and gained
`casting.html`, `watch.html` and `casting_api.py`. §0's route list is a subset.

## Gotchas

**`mw-review.service` used `.venv/bin/python`.** `CLAUDE.md` forbids bare
`python` in every command, snippet and doc example. Corrected to `python3`
2026-11-14; if an installed copy under `/etc/systemd/system/` still has the old
line, reinstall it and `daemon-reload`.

**Locking port 22 to your IP disables the console's browser SSH.** That is the
fallback you would otherwise reach for. You cannot be permanently locked out —
the firewall rule is an API call, editable from the console — but if SSH starts
hanging silently after your home IP changes, check that rule before suspecting
your keys.

**`./mw-deploy` from the repo fails.** It calls `mw-commit-stories` and
`mw-write-running` by bare name, expecting them on `PATH`. Run the §5 install,
then call it as plain `mw-deploy` from anywhere.

**A `%` prompt means you are on the laptop.** zsh on macOS; the box is bash and
a `$`. `mw-deploy` is server-side only — `ssh middlewatch mw-deploy`.
