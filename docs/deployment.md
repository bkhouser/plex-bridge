# Persistent Linux deployment

These templates do not provision services, firewall rules, proxies, tunnels, or cloud accounts. Configure them for your own authorized host.

1. After approving the target host, create a dedicated unprivileged `plex-advisor` service user. Place this project in `/opt/plex-advisor`, create its Python 3.12+ virtual environment, and install the package. Keep code read-only to the service user.
2. Put the final enrolled configuration in `/etc/plex-advisor.env`, owned by the service user and mode 0600. Use absolute `PA_DATABASE_PATH=/var/lib/plex-advisor/plex-advisor.sqlite`. The service creates its own private state directory through systemd.
3. Prefer `PA_MCP_HOST=127.0.0.1`. Use OAuth for tunnel or HTTPS deployments. Complete `discover`, enrollment, `diagnose` and `sync --full` as that same service user before enabling service startup.
4. Review `deploy/plex-advisor.service`; adjust paths, then install and enable it only after authorization. It runs one process and one internal scheduler. Do not also run `schedule` or a timer for that same database.
5. Use the approved tunnel or HTTPS proxy configuration. The Caddy template is provided only for an authorized public HTTPS alternative. An actual domain, certificate, provider and security validation are still needed.

Example Linux setup commands after approval:

```sh
python3.12 -m venv /opt/plex-advisor/.venv
/opt/plex-advisor/.venv/bin/python -m pip install /opt/plex-advisor
# Configure /etc/plex-advisor.env privately before continuing.
sudo systemctl daemon-reload
sudo systemctl enable --now plex-advisor
sudo systemctl status plex-advisor
```

The configuration file is read by systemd; JSON arrays should be single-quoted there, e.g. `PA_LIBRARY_SECTIONS='[1,2]'`. Python `.env` parsing also accepts this form. Keep service logs private. The app emits coarse status codes and suppresses provider HTTP logging. Uvicorn access logging and proxy-header trust are disabled.

## Backups and recovery

SQLite contains private library metadata, viewing history and feedback. Back up before an upgrade. Stop the service and checkpoint SQLite, or use `sqlite3.Connection.backup()` to obtain a consistent snapshot while online. Encrypt backups with your existing backup tooling. The app never reads Plex's database.

Keep feedback-containing backups when rebuilding a cache. Identity changes require a new database, with explicit review before migrating any preferences. There is no destructive reset command. SQLite migrations run transactionally; downgrade against a newer schema is rejected.

A Plex outage preserves the last cache. Review `plex-advisor status` and rerun `sync` after connectivity returns. A failed history import retains its previous cursor; `sync --full` reconciles older history. Do not interpret a stale zero count as current absence.

Use a single worker process. Database writes are serialized and may wait up to 30 seconds; long syncs can delay feedback writes. Read requests remain available through WAL. Scale-out hosting and distributed rate limiting are outside this MVP.
