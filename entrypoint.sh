#!/bin/sh

PUID=${PUID:-1000}
PGID=${PGID:-1000}

CURRENT_PUID=$(id -u botuser)
CURRENT_PGID=$(getent group botgroup | cut -d: -f3)

if [ "$CURRENT_PGID" != "$PGID" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] entrypoint: PGID changed ($CURRENT_PGID -> $PGID) — updating"
    groupmod -g "$PGID" botgroup
    chown -R botuser:botgroup /app
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] entrypoint: PGID $PGID — no changes needed"
fi

if [ "$CURRENT_PUID" != "$PUID" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] entrypoint: PUID changed ($CURRENT_PUID -> $PUID) — updating"
    usermod -u "$PUID" botuser
    chown -R botuser:botgroup /app
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] entrypoint: PUID $PUID — no changes needed"
fi

exec gosu botuser "$@"
