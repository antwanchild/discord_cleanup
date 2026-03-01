#!/bin/sh

PUID=${PUID:-1000}
PGID=${PGID:-1000}

groupmod -o -g "$PGID" botgroup
usermod -o -u "$PUID" botuser

chown -R botuser:botgroup /app

exec su-exec botuser "$@"
