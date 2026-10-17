# jarvis_agent

Explains what went wrong, from the event stream: when an incident opens at ERROR or worse, or a
recovery fails, it cuts a window around the incident (its events, the minutes around it on the same
components, the history of its fault codes, how plans did on them), triages it deterministically,
and publishes an **insight** (`schemas/insight.schema.json`) as an INSIGHT event: hypotheses with
confidence and the event ids that show them, facts, and recommendations.

```text
core/        triggers · windows · triage · context (text for a model) · checks · policy · agent
llm/         heuristic.py: the analyst without a model (4.1); model-backed analysts come in 4.2
adapters/    gateway.py (REST + live stream) · replay.py (recorded events) · ros2.py (publish insights)
```

- **Evidence or it did not happen**: every evidence id must be an event the analyst saw; others are
  dropped and an unsupported hypothesis is capped at 0.3. A recommended plan must exist and the
  arguments it gives must validate.
- **Advice, not action** (autonomy `advise`): recovery recommendations show in the dashboard with a
  Run button; a human runs them, through the same gates as anyone.
- **No future**: history is cut at the incident's first report, so replays and evals are honest.

```bash
ros2 run jarvis_agent jarvis-agent analyze <incident-id> [--json]           # through the gateway
jarvis-agent analyze <incident-id> --replay events.jsonl --plans plans.yaml # no robot, no ROS
ros2 run jarvis_agent jarvis-agent run                                      # the service (bringup starts it)
```

Settings: [`config/agent.yaml`](config/agent.yaml) (`agent_config:=` in the bringup); a gateway token,
if needed, in `$JARVIS_AGENT_TOKEN`. Tests replay a recording of the demo's guided tour
(`test/fixtures/`).
