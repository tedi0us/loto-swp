# Deployment Runbook

Target server runs Docker + Traefik. The app container joins the `proxy` external network;
Traefik handles TLS termination and routing. No nginx/Caddy required.

---

## Prerequisites (one-time server setup)

### 1. Docker & Docker Compose plugin

```bash
# Debian/Ubuntu
curl -fsSL https://get.docker.com | sh
# Verify
docker --version
docker compose version
```

### 2. Traefik stack

Traefik must be running and the `proxy` external network must exist before starting this app.

```bash
# Create the shared network if it doesn't already exist
docker network create proxy
```

Traefik configuration is managed outside this repo. Minimum requirements:
- `websecure` entrypoint on port 443
- `letsencrypt` ACME cert resolver configured
- Container label routing enabled (`--providers.docker=true`, `--providers.docker.exposedbydefault=false`)

### 3. DNS

`loto.example.com` A-record must resolve to the server IP before Let's Encrypt will issue a cert.

### 4. Firewall

Only ports 22 (SSH), 80, and 443 should be reachable externally. Port 8000 must not be exposed.

```bash
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
ufw status
```

---

## Initial Deployment

### 1. Clone the repo

```bash
git clone <repo-url> /opt/loto-swp
cd /opt/loto-swp
```

### 2. Create `.env.production`

```bash
cp .env.production.example .env.production
# Edit and set a real SECRET_KEY:
python3 -c "import secrets; print(secrets.token_hex(64))"
# Paste the output as SECRET_KEY in .env.production
nano .env.production
```

### 3. Prepare the instance directory

The container runs as a non-root system user (`appuser`). The `instance/` directory on the host
holds the SQLite database and must be writable by that user. Build the image first (step 4), then:

```bash
mkdir -p /opt/loto-swp/instance
sudo chown "$(docker run --rm loto-swp-toolkit id -u):$(docker run --rm loto-swp-toolkit id -g)" /opt/loto-swp/instance
```

### 4. Build the image

```bash
docker build --network=host -t loto-swp-toolkit .
# Confirm non-root:
docker run --rm loto-swp-toolkit id
# Expected: uid=<appuser> (NOT uid=0)
```

### 5. Start the app

```bash
docker compose --env-file .env.production up -d
```

### 6. Smoke test

```bash
# Via Traefik (requires DNS + cert — may take 30–60s for first cert issue):
curl -s -o /dev/null -w "%{http_code}" https://loto.example.com/
# Expected: 302

# Direct to container while waiting for cert:
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/
# Expected: 302
```

### 7. Create the first admin user

```bash
docker compose exec app python scripts/create_user.py
```

Choose the `it_admin` role and answer `Y` to require a password change on first login.
Every other account can then be created from **Users → New** in the web app.

---

## Updating the App

```bash
cd /opt/loto-swp
git pull origin main
docker build --network=host -t loto-swp-toolkit .
docker compose --env-file .env.production up -d --no-deps --build app
```

The SQLite DB in `instance/` is preserved across rebuilds via the volume mount.

---

## Useful Commands

| Task | Command |
|------|---------|
| View logs | `docker compose logs -f app` |
| Stop | `docker compose down` |
| Restart | `docker compose restart app` |
| Shell in container | `docker compose exec app sh` |
| Check container user | `docker compose exec app id` |
| DB backup | `cp instance/app.db instance/app.db.bak` |

---

## Traefik Label Reference

The labels in `docker-compose.yml` that control routing:

```yaml
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.loto.rule=Host(`loto.example.com`)"
  - "traefik.http.routers.loto.entrypoints=websecure"
  - "traefik.http.routers.loto.tls.certresolver=letsencrypt"
  - "traefik.http.services.loto.loadbalancer.server.port=8000"
```

To change the hostname, update the `rule` label and redeploy.

---

## Rollback

```bash
cd /opt/loto-swp
git log --oneline -10          # find the target commit
git checkout <commit-hash>
docker build --network=host -t loto-swp-toolkit .
docker compose --env-file .env.production up -d --no-deps --build app
```
