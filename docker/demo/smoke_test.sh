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
api() { curl -fsS "http://localhost:8080/api/v1$1"; }
json() { python3 -c "import json, sys; d = json.load(sys.stdin); print($1)"; }

rm -rf demo-data
docker compose up -d --no-build
./demo wait || fail "Jarvis never became healthy"
for _ in $(seq 1 20); do [ -f demo-data/lake/status.json ] && break; sleep 1; done
[ -f demo-data/lake/status.json ] || fail "the lake service is not running"
for _ in $(seq 1 30); do [ "$(api /health 2>/dev/null | json 'd["status"]')" = ok ] && break; sleep 1; done
[ "$(api /health | json 'd["status"]')" = ok ] || fail "the gateway is not healthy"
expect 'id="root"' "$(curl -fsS http://localhost:8080/)" "the dashboard is not served"
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

# The same faults through the gateway, and an operator command there: attributed in the audit trail.
estop=$(api /faults | json 'next(f["fault_id"] for f in d["faults"] if f["code"] == "safety.estop.engaged")') ||
  fail "the e-stop is not in the gateway's faults"
curl -fsS -X POST -H "Content-Type: application/json" -d '{"note": "smoke test"}' \
  "http://localhost:8080/api/v1/faults/$estop:ack" >/dev/null || fail "ack through the gateway failed"
expect "demo acknowledged safety\.estop\.engaged" \
  "$(api /audit | json '"\n".join(e.get("message", "") for e in d["events"])')" "the ack is not audited"
succeeded=$(api /recoveries | json 'sum(x["outcome"] == "SUCCEEDED" for x in d["history"])')
[ "$succeeded" -ge 1 ] || fail "the gateway shows no successful recovery"
# The agent explained the lidar stall: the restart did not help (it analyzes 20 s after a trigger).
for _ in $(seq 1 30); do
  insights=$(api /insights | json '"\n".join(e["payload"]["title"] for e in d["insights"])')
  grep -q "restart-lifecycle-node did not fix it" <<<"$insights" && break
  sleep 2
done
expect "sensor\.scan\.stale on lidar: restart-lifecycle-node did not fix it" "$insights" "no insight on the stall"
# The model path of the image (LiteLLM, hash-locked) on the same stall, with no key or internet: an
# OpenAI-compatible stub in the container answers.
stall=$(api /insights | json 'next(e["correlation_id"] for e in d["insights"] if "did not fix" in e["payload"]["title"])')
docker compose cp docker/demo/llm_stub.py demo:/tmp/llm_stub.py >/dev/null
docker compose exec -d demo python3 /tmp/llm_stub.py 18099
printf '%s\n' 'schema: jarvis.agent/v1' \
  'model: { name: openai/jarvis-smoke, api_base: "http://127.0.0.1:18099/v1", assume_tools: true }' |
  docker compose exec -T demo sh -c 'cat >/tmp/agent-smoke.yaml' # demo-data belongs to the container
sleep 1
out=$(docker compose exec -T -e OPENAI_API_KEY=smoke demo bash -c "source /opt/jarvis/setup.bash && \
  ros2 run jarvis_agent jarvis-agent --config /tmp/agent-smoke.yaml analyze $stall") ||
  fail "the agent failed with a model: $out"
echo "$out"
expect "The stub model read [1-9][0-9]* event" "$out" "the model's insight was not used"
expect "llm analyst · openai/jarvis-smoke" "$out" "the agent did not go through the model"

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
