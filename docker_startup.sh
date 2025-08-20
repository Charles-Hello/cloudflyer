#!/bin/bash

echo "Starting Cloudflyer with arguments: $@"

exec /dockerstartup/vnc_startup.sh  > /dev/null 2>&1 &

cd /app
/app/venv/bin/python -m cloudflyer $@
