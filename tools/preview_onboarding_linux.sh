#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <bake|splash|welcome|whats-new> <output.png>" >&2
  exit 2
fi

mode="$1"
output="$2"
case "$mode" in
  bake|splash|welcome|whats-new) ;;
  *) echo "unsupported mode: $mode" >&2; exit 2 ;;
esac

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
content_root="$repository_root/cloth_next/resources/onboarding"
mkdir -p "$(dirname "$output")"

if [[ "$mode" == "bake" ]]; then
  arguments=(-m companion.app --mode bake)
  kill_after_capture=1
else
  arguments=(
    -m companion.app
    --mode "$([[ "$mode" == "splash" ]] && echo welcome || echo "$mode")"
    --version 2.8.9
    --content-root "$content_root"
  )
  if [[ "$mode" == "splash" ]]; then
    arguments+=(--splash-ms 6000)
  fi
  kill_after_capture=0
fi

export CLOTH_NEXT_COMPANION_AUTO_CLOSE_MS=5000
export REPOSITORY_ROOT="$repository_root"
export PREVIEW_OUTPUT="$output"
export PREVIEW_ARGUMENTS="${arguments[*]}"
export PREVIEW_KILL_AFTER_CAPTURE="$kill_after_capture"

xvfb-run -a -s "-screen 0 1024x768x24" bash -c '
  openbox >/tmp/cloth-next-openbox.log 2>&1 &
  cd "$REPOSITORY_ROOT"
  # Arguments contain repository-controlled paths without shell metacharacters.
  python3 $PREVIEW_ARGUMENTS &
  application_pid=$!
  xdotool search --sync --name "Cloth NeXt" >/dev/null
  xdotool mousemove 0 0
  sleep 1
  import -window root "$PREVIEW_OUTPUT"
  if [[ "$PREVIEW_KILL_AFTER_CAPTURE" == 1 ]]; then
    kill "$application_pid"
    wait "$application_pid" || true
  else
    wait "$application_pid"
  fi
'

magick "$output" -trim "$output"
echo "$output"
