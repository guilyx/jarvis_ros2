#!/usr/bin/env bash
# Smoke test of the Docker demo, run by CI: start it, drive it with the commands people type, check
# Jarvis saw every scenario, stop it, and check the recordings were finalized.
# Needs the jarvis_ros2:demo image (docker compose build, or the CI build step).
set -euo pipefail
cd "$(dirname "$0")/../.."

fail() {
  echo "SMOKE TEST FAILED: $*" >&2
  docker compose logs --tail 100 demo >&2 || true
  exit 1
}
screen() { docker compose exec -T demo monitor --once --history 300; }
open_faults() { screen | sed -n '/^OPEN FAULTS/,/^RECENT EVENTS/p'; }
expect() { grep -Eq "$1" <<<"$2" || fail "$3"; }

rm -rf demo-data
docker compose up -d --no-build
./demo wait || fail "Jarvis never became healthy"
expect "none, all good" "$(open_faults)" "faults on a healthy start"
sleep 3 # a full rate window of the lidar relay, where a startup glitch would show
if grep -q " RAISE " <<<"$(screen)"; then fail "a fault was raised on a healthy start"; fi

./demo inject tour --pace 0.5
out=$(screen)
echo "$out"
expect "TOUR +Tour done" "$out" "tour narration missing"
expect "sensor\.scan\.stale → ACTIVE" "$out" "lidar stall not confirmed"
expect "jarvis\.fault\.muted" "$out" "rate symptom not muted"
expect "jarvis\.blackbox\.captured" "$out" "no black-box snapshot"
expect "diag\.fault_injector\.motor_driver → ACTIVE" "$out" "diagnostics fault missing"
expect "ros\.log\.error → ACTIVE" "$out" "rosout fault missing"
expect "LATCHED +CRIT +safety\.estop\.engaged" "$out" "e-stop not latched"

expect "cleared safety\.estop\.engaged" "$(./demo clear)" "operator clear failed"
if grep -q "safety.estop.engaged" <<<"$(open_faults)"; then fail "e-stop still open after the clear"; fi

docker compose stop
compgen -G "demo-data/bags/*/metadata.yaml" >/dev/null || fail "recording not finalized"
grep -q "/jarvis/events" demo-data/bags/*/metadata.yaml || fail "/jarvis/events not recorded"
compgen -G "demo-data/blackbox/*/*.mcap" >/dev/null || fail "no black-box file"
echo "SMOKE TEST PASSED"
