#!/bin/sh

set -e

python manage.py migrate --noinput

PUID=${PUID:-1000}
PGID=${PGID:-1000}

groupmod -o -g "$PGID" abc
usermod -o -u "$PUID" abc

chown abc:abc /yamtrack
chown -R abc:abc db
chown -R abc:abc staticfiles

exec supervisord -c /etc/supervisord.conf
