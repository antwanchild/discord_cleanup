#!/bin/sh

PUID=${PUID:-1000}
PGID=${PGID:-1000}

if [ "$(id -u botuser)" != "$PUID" ]; then
    usermod -u "$PUID" botuser
else
    echo "PUID already set to $PUID — no changes needed"
fi

if [ "$(getent group botgroup | cut -d: -f3)" != "$PGID" ]; then
    groupmod -g "$PGID" botgroup
else
    echo "PGID already set to $PGID — no changes needed"
fi

chown -R botuser:botgroup /app

exec gosu botuser "$@"