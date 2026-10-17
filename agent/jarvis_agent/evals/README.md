# Evals

The agent on labelled incidents, scored. Each case is a recorded incident (or a question) with what a
good answer says; `jarvis-agent eval` runs them through the agent as it ships (replay adapter, no
ROS, no gateway) and grades what comes out.

```bash
jarvis-agent --heuristic eval                                   # the baseline: free, deterministic
jarvis-agent --model openrouter/<vendor>/<model> eval \
  --judge openrouter/<vendor>/<model> --max-cost-usd 2 \
  --results evals/results.tsv --note "what changed"             # a model, graded by a judge
jarvis-agent eval --case lidar-stall-restart-fails --report out.json   # one case, every detail
```

(`PYTHONPATH=agent/jarvis_agent python -m jarvis_agent.cli ...` from the repository root works too.)
It exits 1 below `--min-score`, 2 on a bad corpus, 3 when the cost cap cut the run short.

## Grades

| Grader | Weight | What it checks |
|--------|--------|----------------|
| `root_cause` | 0.35 | The most confident hypothesis cites an event of the labelled fault, and says what the label says: its `mentions` (one word of each group), or, with `--judge`, an LLM grading against `root_cause.text` with a rubric (0, 1 or 2) |
| `evidence` | 0.15 | Of what the analyst cited, before the harness checks, the share it had been shown |
| `must_cite` | 0.20 | One event of each `must_cite` group is cited |
| `plans` | 0.20 | No forbidden plan recommended (0), an acceptable one when one is required, nothing unexpected (0.5) |
| `escalation` | 0.05 | An ESCALATE recommendation when, and only when, the label wants one |
| `assessment` | 0.05 | The severity it gives is one the label accepts |

A case's score is the weighted mean of the graders that apply to it; the run's is the mean of its
cases. The rules are a proxy for words; the judge is better, and costs a call per case.

## The gate

A change to the prompt, the tools, the context or the model merges only if the score holds or
improves: run the eval before and after, and commit the rows to [`results.tsv`](results.tsv) with
the change. The heuristic analyst's scores are a test (`test/test_evals.py`): CI fails when a case
gets worse. The nightly job (`.github/workflows/evals.yml`) runs the heuristic analyst, and a model
when the repository sets `JARVIS_EVAL_MODEL` (and `JARVIS_EVAL_JUDGE`) with its provider's key as
a secret; its artifact has the report, the rows and the model's traces.

## The corpus

`corpus/<case>/label.yaml` (schema `jarvis.eval/v1`, [`corpus.py`](../jarvis_agent/evals/corpus.py)
documents every key), and the events: the case's own `events.jsonl` (a `ros2 jarvis export`), or a
shared recording in `recordings/`. The seed cases come from the demo's guided tour
(`recordings/demo_tour.jsonl`, recorded through the gateway: `ros2 launch jarvis_demo
demo.launch.py`, then `inject tour`); its narration (`demo.tour.*`) is dropped, since it gives the
answers away.

| Case | Trigger | The root cause |
|------|---------|----------------|
| `estop-pressed-and-cleared` | incident | the e-stop, latched, cleared by an operator |
| `relay-wedge-restart-fixes` | incident | the relay hung; the restart fixed it |
| `relay-wedge-again` | incident | the relay hung again; the restart fixed it again |
| `lidar-stall-restart-fails` | recovery failed | the lidar itself stopped; the restart cannot help; escalated |
| `motor-overcurrent` | incident | a transient overcurrent of the left motor |
| `node-logs-errors` | incident | one node logs "costmap update failed" in a loop |
| `estop-latched` | incident | the e-stop, latched, waiting for an operator |
| `why-did-it-stop` | question | the latched e-stop, among older faults |
| `what-happened-to-the-lidar` | question | two episodes, two causes |

Adding a case: put the events in the case's directory (`ros2 jarvis export <bag> -o events.jsonl`
from the black box or the recorder's bags, or the gateway's `/api/v1/events`) with the plans then
(`plans.json`, from `/api/v1/plans`), write the label (cut it with `at:` where the live agent would
have analyzed: 20 s after the trigger by default), and run `jarvis-agent --heuristic eval --case
<name>`: the loader refuses a label that names an event, an incident or a plan its recording does
not have. Then add the heuristic's score to the baseline in `test/test_evals.py`.
