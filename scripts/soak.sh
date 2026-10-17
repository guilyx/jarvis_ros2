#!/usr/bin/env bash
# Jarvis under load for a while (docs/design/01-requirements.md §5): the bringup as it ships (recorder,
# black box, lake, gateway; no agent), jarvis_load at a steady rate for SOAK_S seconds, then a peak
# with a burst. The steady part must lose nothing, keep p99 under 100 ms and the RSS under
# MAX_RSS_MB; the peak is reported. The reports (JSON and text) land in OUT_DIR.
#
#   scripts/soak.sh [OUT_DIR] [SOAK_S] [RATE] [MAX_RSS_MB]    (in a sourced, built workspace)
set -euo pipefail

out=${1:-soak-out}
soak_s=${2:-900}
rate=${3:-500}
max_rss_mb=${4:-700}
mkdir -p "$out"
data=$(mktemp -d)
export ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-42}

ros2 launch jarvis_bringup jarvis.launch.py data_dir:="$data" agent:=false >"$out/launch.log" 2>&1 &
launch=$!
trap 'kill -INT "$launch" 2>/dev/null || true; wait "$launch" 2>/dev/null || true; rm -rf "$data"' EXIT
sleep 15

status=0
ros2 run jarvis_ros jarvis_load --rate "$rate" --seconds "$soak_s" --check --max-p99-ms 100 \
  --max-rss-mb "$max_rss_mb" --json "$out/soak.json" | tee "$out/soak.txt" || status=1
ros2 run jarvis_ros jarvis_load --rate 5000 --seconds 30 --burst 20000 --sources 500 \
  --json "$out/peak.json" | tee "$out/peak.txt" || true
exit "$status"
