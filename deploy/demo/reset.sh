#!/bin/sh
# Saves or restores the demo's starting point: its Whenevers, tagged people, places and alert names.
# Many visitors share one demo, so after a busy day the owner can put it back as it was.
# Usage on the server: sudo sh reset.sh save     (after setting the demo up as it should start)
#                      sudo sh reset.sh restore  (puts that back; logs and clips stay)
set -eu

app=/srv/whenever/demo/app
seed=/srv/whenever/demo/seed.tar.gz

case "${1:-}" in
  save)
    # The demo writes its files all the time, so it stops for the few seconds of the copy.
    systemctl stop whenever-demo-cameras whenever-demo
    tar -C "$app" -czf "$seed" data rules
    chown whenever:whenever "$seed"
    systemctl start whenever-demo whenever-demo-cameras
    echo "Saved the demo's starting point."
    ;;
  restore)
    test -f "$seed" || { echo "No saved starting point. Run: sudo sh reset.sh save"; exit 1; }
    systemctl stop whenever-demo-cameras whenever-demo
    rm -rf "$app/data" "$app/rules"
    tar -C "$app" -xzf "$seed"
    chown -R whenever:whenever "$app/data" "$app/rules"
    systemctl start whenever-demo whenever-demo-cameras
    systemctl is-active whenever-demo whenever-demo-cameras
    ;;
  *)
    echo "Usage: sudo sh reset.sh save|restore"; exit 1
    ;;
esac
