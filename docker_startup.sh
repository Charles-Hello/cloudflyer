#!/bin/bash

exec /dockerstartup/vnc_startup.sh  > /dev/null 2>&1 &

cd /app
cloudflyer $@