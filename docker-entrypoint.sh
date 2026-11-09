#!/bin/sh
set -e

# The HA Supervisor mounts the add-on data dir root-owned and writes
# options.json into it; fix ownership, then run as the unprivileged user.
[ -d /data ] && chown -R ff:ff /data || true

# The Supervisor injects SUPERVISOR_TOKEN into every add-on container;
# map it to the app's FF_ names and enable ingress auth mode. All
# overridable — standalone runs (no Supervisor env) stay standalone.
if [ -n "$SUPERVISOR_TOKEN" ]; then
    export FF_INGRESS="${FF_INGRESS:-true}"
    export FF_SUPERVISOR_TOKEN="${FF_SUPERVISOR_TOKEN:-$SUPERVISOR_TOKEN}"
fi

exec runuser -u ff -- uvicorn app.main:app --host 0.0.0.0 --port 8099
