# jarvis_agent

Explains what went wrong, from the event stream: when an incident opens at ERROR or worse, or a
recovery fails, it cuts a window around the incident (its events, the minutes around it on the same
components, the history of its fault codes, how plans did on them), triages it deterministically,
and publishes an **insight** (`schemas/insight.schema.json`) as an INSIGHT event: hypotheses with
confidence and the event ids that show them, facts, and recommendations.

```text
core/        triggers · windows · triage · context (text for a model) · analyst (the model's tool
             loop) · checks · policy · agent
llm/         litellm_client.py (any provider or a local model) · heuristic.py (no model) · fake.py (tests)
tools/       toolbox.py: what the model may read (events, faults, incidents, plans, rules); nothing else
adapters/    gateway.py (REST + live stream) · replay.py (recorded events) · ros2.py (publish insights)
```

- **Evidence or it did not happen**: every evidence id must be an event the analyst saw; others are
  dropped and an unsupported hypothesis is capped at 0.3. A recommended plan must exist and the
  arguments it gives must validate.
- **Advice, not action** (autonomy `advise`, the default): recovery recommendations show in the
  dashboard with a Run button; a human runs them, through the same gates as anyone.
- **Proposals go through the one gate** (autonomy `supervised` or `autonomous`, with a model): the
  model may `propose_recovery` a catalog plan, with every required argument and the events that
  call for it. The agent's node sends it as an `ExecuteRecovery` goal, requester AGENT; the
  orchestrator applies its own `agent_autonomy` ceiling, the plan's `agent_policy`, the confidence
  threshold and every other gate. A supervised proposal waits for an operator (Approve / Reject on
  the insight's card); the insight lists its execution ids. The heuristic analyst never proposes,
  and `analyze` from the command line only advises.
- **No future**: history is cut at the incident's first report, so replays and evals are honest.
- **The model is optional**: without one, offline, over budget, or when its answer stays invalid
  after one repair turn, the heuristic analyst answers and the insight says why.

## Asking it

The service answers the `Analyze` action (`/jarvis/agent/analyze`): explain an incident, or answer
a question about the last minutes (or a question about an incident). The gateway's
`POST /api/v1/agent/ask` calls it and streams the progress as server-sent events, then the insight;
the dashboard's Agent view has the Ask box.

## A model

Any [LiteLLM](https://docs.litellm.ai/) model string, with its provider's key in the environment:

```bash
pip install --require-hashes -r requirements-llm.txt   # LiteLLM, hash-locked (the Docker image has it)
export OPENROUTER_API_KEY=...                           # or ANTHROPIC_API_KEY, OPENAI_API_KEY, ...
jarvis-agent --model openrouter/<vendor>/<model> analyze <incident-id>
```

or in `agent.yaml`: `model: { name: ..., api_base: ... }` (a LiteLLM proxy, Ollama or vLLM on the
LAN: `ollama/<model>`, or `openai/<model>` with `api_base`). The agent refuses a model LiteLLM does
not know to call tools (`assume_tools: true` if it does). Keys never go in the file: the
provider's variable, `model.api_key_env`, or a mounted file named by `$JARVIS_AGENT_LLM_KEY_FILE`.

The model gets the triaged window and read-only tools (`query_events`, `get_event`,
`get_fault_history`, `get_incident`, `list_recovery_plans`, `get_rule`, ...), at most 12 calls, and
must finish with `submit_insight`. What it says goes through the same checks: evidence ids it was
never shown are dropped, unknown plans and bad arguments too. Budgets: $1 an hour, $10 a day and
250k tokens an analysis by default (`budget:`). Triage that is conclusive alone (one fault, fixed by
a plan) skips the model. Each analysis leaves a trace in `$JARVIS_DATA/agent_traces/` (hashes, tool
calls, tokens, cost, latency; no event content).

`requirements-llm.txt` pins LiteLLM and everything it needs with hashes, using releases at least
two weeks old when locked (LiteLLM had malicious releases, 1.82.7 and 1.82.8): regenerate it as its
header says, and read the diff.

```bash
ros2 run jarvis_agent jarvis-agent analyze <incident-id> [--json]           # through the gateway
jarvis-agent analyze <incident-id> --replay events.jsonl --plans plans.yaml # no robot, no ROS
ros2 run jarvis_agent jarvis-agent run                                      # the service (bringup starts it)
```

Settings: [`config/agent.yaml`](config/agent.yaml) (`agent_config:=` in the bringup); a gateway token,
if needed, in `$JARVIS_AGENT_TOKEN`. Tests replay a recording of the demo's guided tour
(`evals/recordings/`).

## Evals

`jarvis-agent eval` scores the analyst on labelled incidents ([`evals/`](evals/README.md)): root
cause (rules, or an LLM judge), evidence validity, must-cite recall, plans, escalation and severity,
under a cost cap. A prompt or model change merges only if the score holds (`evals/results.tsv`);
the heuristic analyst's scores are a test, and a nightly job runs a model when one is configured.

```bash
jarvis-agent --heuristic eval                                        # the baseline
jarvis-agent --model <model> eval --judge <model> --results evals/results.tsv --note "..."
```
