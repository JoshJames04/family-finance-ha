#!/bin/sh
set -e

# The HA Supervisor mounts the add-on data dir root-owned and writes
# options.json into it; fix ownership, then run as the unprivileged user.
[ -d /data ] && chown -R ff:ff /data || true

exec runuser -u ff -- uvicorn app.main:app --host 0.0.0.0 --port 8099
