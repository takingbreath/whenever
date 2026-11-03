#!/bin/sh
# Copies this folder's code to a Linux server and restarts Whenever there.
# Usage: deploy/deploy.sh SSH_HOST
# It sends only the files that Git would publish. Keys, logs and rules stay where they are.
set -eu

host="${1:?Usage: deploy/deploy.sh SSH_HOST}"
cd "$(dirname "$0")/.."
stage="/tmp/whenever-deploy"

git ls-files --cached --others --exclude-standard > /tmp/whenever-files.txt
rsync -a --delete --files-from=/tmp/whenever-files.txt . "$host:$stage/"
rm /tmp/whenever-files.txt
ssh "$host" "sudo rsync -a --chown=whenever:whenever $stage/ /srv/whenever/app/ && rm -rf $stage && \
  sudo -u whenever /srv/whenever/venv/bin/pip install --quiet --upgrade --target /srv/whenever/app/vendor \
    -r /srv/whenever/app/requirements.txt && sudo systemctl restart whenever && sleep 2 && \
  systemctl is-active whenever"
