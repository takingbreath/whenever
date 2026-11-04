#!/bin/sh
# One-time setup of the demo Whenever, on the server, after deploy/deploy.sh has put the code in
# /srv/whenever/app. Later deploys update the demo too.
# Usage on the server: sudo sh setup.sh "demo.example.com" /path/to/code.txt
# The code file holds the demo's access code. The demo gets its own copy of the Meta key, made here on
# the server, so the key never travels or shows.
set -eu

hosts="${1:?the demo host names, with commas}"
code_file="${2:?a file with the demo access code}"
demo=/srv/whenever/demo

mkdir -p "$demo/app" "$demo/media" "$demo/footage"
rsync -a --exclude data --exclude logs --exclude rules --exclude vendor --exclude models \
  --exclude access.env --exclude .env --exclude actions.env --exclude camera.env --exclude camera.rtsp.env \
  --exclude ezviz_token.json --exclude __pycache__ /srv/whenever/app/ "$demo/app/"
ln -sfn /srv/whenever/app/vendor "$demo/app/vendor"
ln -sfn /srv/whenever/app/models "$demo/app/models"
install -m 600 /srv/whenever/app/.env "$demo/app/.env"
install -m 600 "$code_file" "$demo/app/access.env"
cp "$demo/app/deploy/demo/camera.env.demo" "$demo/app/camera.env"
if [ ! -f "$demo/settings.env" ]; then
  sed "s|^WHENEVER_HOST=.*|WHENEVER_HOST=$hosts|" "$demo/app/deploy/demo/settings.env.example" > "$demo/settings.env"
  # The owner's one-day extras count for the demo's share too.
  grep '^WHENEVER_SPEND_EXTRA=' /srv/whenever/settings.env >> "$demo/settings.env" || true
fi
chown -R whenever:whenever "$demo"

# The real server counts the demo's spend in the owner's budget.
grep -q '^WHENEVER_SPEND_ALSO=' /srv/whenever/settings.env || \
  echo "WHENEVER_SPEND_ALSO=$demo/app/data/spend.json" >> /srv/whenever/settings.env

cp "$demo/app/deploy/demo/"*.service /etc/systemd/system/
sed "s|^HOST {|$hosts {|" "$demo/app/deploy/demo/demo.caddy" > /etc/caddy/whenever-demo.caddy
grep -q 'import whenever-demo.caddy' /etc/caddy/Caddyfile || echo 'import whenever-demo.caddy' >> /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile

systemctl daemon-reload
systemctl enable --now whenever-demo-media whenever-demo whenever-demo-cameras
systemctl restart whenever
systemctl reload caddy
systemctl is-active whenever whenever-demo whenever-demo-media whenever-demo-cameras
