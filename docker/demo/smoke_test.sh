#!/usr/bin/env bash
# Smoke test of the Docker demo, run by CI: start it, drive it with the commands people type, check
# Jarvis saw and handled every scenario, stop it, check the recordings were finalized, and replay the
# recording through the rules: the backtest must reproduce every live decision.
# Needs the jarvis_ros2:demo image (docker compose build, or the CI build step).
set -euo pipefail
cd "$(dirname "$0")/../.."

fail() {
  echo "SMOKE TEST FAILED: $*" >&2
  docker compose logs --tail 100 demo >&2 || true
  exit 1
}
screen() { docker compose exec -T demo monitor --once --history 300; }
open_faults() { screen | sed -n '/^OPEN FAULTS/,/^RECOVERY/p'; }
expect() { grep -Eq "$1" <<<"$2" || fail "$3"; }

rm -rf demo-data
docker compose up -d --no-build
./demo wait || fail "Jarvis never became healthy"
for _ in $(seq 1 20); do [ -f demo-data/lake/status.json ] && break; sleep 1; done
[ -f demo-data/lake/status.json ] || fail "the lake service is not running"
expect "none, all good" "$(open_faults)" "faults on a healthy start"
sleep 3 # a full rate window of the lidar relay, where a startup glitch would show
if grep -q " RAISE " <<<"$(screen)"; then fail "a fault was raised on a healthy start"; fi

./demo inject tour --pace 0.5
out=$(screen)
echo "$out"
expect "TOUR +Tour done" "$out" "tour narration missing"
expect "sensor\.scan\.stale → ACTIVE" "$out" "lidar stall not confirmed"
expect "lidar-stale-restart matched" "$out" "the restart rule did not fire"
expect "jarvis\.recovery\.succeeded +restart-lifecycle-node succeeded" "$out" "wedged relay not recovered"
expect "jarvis\.recovery\.failed +restart-lifecycle-node failed" "$out" "the stall's restart did not fail"
expect "jarvis\.notify +escalated to operator" "$out" "the failed recovery was not escalated"
expect "jarvis\.fault\.muted" "$out" "rate symptom not muted"
expect "jarvis\.blackbox\.captured" "$out" "no black-box snapshot"
expect "diag\.fault_injector\.motor_driver → ACTIVE" "$out" "diagnostics fault missing"
expect "ros\.log\.error → ACTIVE" "$out" "rosout fault missing"
expect "LATCHED +CRIT +safety\.estop\.engaged" "$out" "e-stop not latched"

expect "cleared safety\.estop\.engaged" "$(./demo clear)" "operator clear failed"
if grep -q "safety.estop.engaged" <<<"$(open_faults)"; then fail "e-stop still open after the clear"; fi

# The relay keeps hanging: Jarvis restarts it 3 times in 10 minutes (the tour used some), then stops
# and calls a human. The operator restarts it by hand, through the same gates.
for _ in 1 2 3 4; do
  sleep 3 # past the rule's cooldown
  ./demo inject scan-wedge --quiet --timeout 10 || break
done
out=$(screen)
expect "jarvis\.rule\.suppressed +Rule lidar-stale-restart" "$out" "the restart rule was never suppressed"
expect "jarvis\.notify +The lidar on demo-bot keeps hanging" "$out" "no escalation after the suppression"
restart=$(./demo jarvis recover restart-lifecycle-node -a node=/demo/scan_relay -a component=lidar \
  --operator smoke-test) || fail "the operator's restart did not succeed: $restart"
echo "$restart"
expect "SUCCEEDED +restart-lifecycle-node +operator smoke-test" "$(screen)" "operator restart not shown"

docker compose stop
compgen -G "demo-data/bags/*/metadata.yaml" >/dev/null || fail "recording not finalized"
grep -q "/jarvis/events" demo-data/bags/*/metadata.yaml || fail "/jarvis/events not recorded"
compgen -G "demo-data/blackbox/*/*.mcap" >/dev/null || fail "no black-box file"

# Same decisions when the recording is replayed through the rules (the container is stopped: run one).
bag=$(basename "$(dirname "$(compgen -G "demo-data/bags/*/metadata.yaml" | head -1)")")
docker compose run --rm --no-deps -T demo ros2 jarvis backtest "/var/lib/jarvis/bags/$bag" \
  --rules /opt/jarvis/share/jarvis_demo/config/rules --robot demo-bot --expect-same ||
  fail "the backtest does not reproduce the live decisions"

# The recording as history: the lake converts the closed bag, and its views tell the same story.
lake() { docker compose run --rm --no-deps -T demo ros2 run jarvis_lake jarvis-lake --data-dir /var/lib/jarvis "$@"; }
lake sync || fail "the lake could not convert the recording"
history=$(lake sql "SELECT (
    (SELECT count(*) FILTER (WHERE outcome = 'SUCCEEDED') >= 3
        AND count(*) FILTER (WHERE outcome = 'FAILED') >= 1 FROM executions)
    AND (SELECT count(*) FILTER (WHERE escalated) >= 1 FROM incidents)
    AND (SELECT count(*) >= 1 FROM operator_actions WHERE code = 'fault.clear'))::INTEGER AS ok" \
  --format csv)
echo "$history"
expect "^1$" "$(tail -1 <<<"$history")" "the lake's history does not match the run"
echo "SMOKE TEST PASSED"
