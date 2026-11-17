#!/bin/bash
# Finishes an update the app has already swapped in (UpdateInstall: a whole copy beside the app, exchanged
# in one step), once the app has quit:  install-update.sh <pid> <old app> <target app> <relaunch>
# The old copy goes, and the new app starts as asked: 1 in the background, window in the background with its
# window open again, front in front with its window, 0 not at all.
set -u
pid=$1 old=$2 target=$3 relaunch=$4
open_cmd=${CLAUDE_SYNC_OPEN:-/usr/bin/open}
wait_seconds=${CLAUDE_SYNC_WAIT_SECONDS:-60}

say() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }

for _ in $(seq 1 $((wait_seconds * 4))); do
  kill -0 "$pid" 2>/dev/null || break
  sleep 0.25
done
if kill -0 "$pid" 2>/dev/null; then
  say "the app did not quit; its old copy stays at $old until the next start"
  exit 1
fi
rm -rf "$old"
say "installed $target"
case "$relaunch" in
  1) "$open_cmd" -g "$target" --args --after-update ;;
  window) "$open_cmd" -g "$target" --args --after-update --show-window ;;
  front) "$open_cmd" "$target" --args --after-update --show-window ;;
esac
exit 0
