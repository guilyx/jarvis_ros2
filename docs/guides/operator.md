# Running robots with Jarvis

For the people on shift: what Jarvis shows, what to do when something is wrong, and the controls
you have. The dashboard is at the gateway's address (<http://127.0.0.1:8080> on the robot unless
your team set up another); everything in it is also in `ros2 jarvis` and the API.

1. [Signing in](#1-signing-in)
2. [What needs you](#2-what-needs-you)
3. [Faults](#3-faults)
4. [Incidents](#4-incidents)
5. [Recoveries and approvals](#5-recoveries-and-approvals)
6. [The kill switch](#6-the-kill-switch)
7. [Rules](#7-rules)
8. [Asking the agent](#8-asking-the-agent)
9. [Recordings](#9-recordings)
10. [What was done, by whom](#10-what-was-done-by-whom)
11. [From the command line](#11-from-the-command-line)

## 1. Signing in

Three roles: **viewer** sees everything, **operator** also acknowledges and clears faults, closes
incidents, runs and approves recoveries and asks the agent, **admin** also sets the recovery mode
and changes rules. The dashboard hides what your role can't do.

- **With a token**: *Identity* (top right) → paste the token your team gave you. It stays in this
  browser until you *Forget token*. What you do is recorded under the token's name.
- **With single sign-on**, if your team set it up (security guide): you are signed in already,
  with the role of your groups.
- **Without either** you are `anonymous`, a viewer (or refused, if the robot requires sign-in).

The header shows the link to the robot: **LIVE**, **CONNECTING**, **RECONNECTING** (it catches up
with everything it missed when it's back) or **NO ACCESS** (your token is refused).

## 2. What needs you

**Overview** is the shift view: active faults by severity, open incidents, how often recovery
fixed things on its own, the mean time to repair, and the event rate. Its *Needs attention* list
is what's waiting for a human:

- **approvals** a recovery is waiting for (they expire after 5 minutes);
- **escalated incidents**: Jarvis tried, it didn't work, someone has to look;
- **latched and critical faults** that aren't symptoms of another.

The bell in the header counts approvals and the latched or critical faults nobody acknowledged
yet. Every view keeps its filters in the URL: copy the address to share exactly what you see.

## 3. Faults

A fault is a problem Jarvis tracks from first report to clear, for one code on one component.

| State | Means |
|-------|-------|
| `PENDING` | Reported, not confirmed yet (the catalog's debounce: e.g. it must persist 1 s) |
| `ACTIVE` | Confirmed. At ERROR and above it opens an incident |
| `LATCHED` | The cause went away, but this fault waits for a human (an e-stop, say) |
| `CLEARED` | Over: healed on its own, or cleared by someone |

**Faults** lists the active ones (and their history). A dimmed row is a **symptom**, muted under
the fault that caused it (a navigation abort under a blind lidar): work on the root, and its
symptoms clear with it.

Open a fault for its detail: when it started and was confirmed, how often it was reported, its
**freeze frame** (what the reporter attached, and the robot's state when it confirmed), its
**runbook** (what to check and do, from your team's catalog), whether its **black box** was saved
(the file is under System → Recordings), its transitions, and the recoveries that ran for it.

- **Acknowledge** = "I'm on it". The fault stays open; the bell stops counting it; your name and
  note are recorded.
- **Clear** = "it's dealt with". It closes the fault now, latched or not (a latched one needs a
  reason: what you checked), and the symptoms it muted with it. If the cause is still there, the
  fault comes back.
- **Recover** opens *Run plan* with the fault's component and code filled in.

## 4. Incidents

An incident groups what went wrong together: the faults of one component within two minutes,
their symptoms, the recoveries, the agent's analysis and what people did.

| State | Means |
|-------|-------|
| `OPEN` | A fault at ERROR or above confirmed |
| `RECOVERING` | A recovery plan is running for it |
| `ESCALATED` | Recovery gave up, or a rule asked for a human |
| `RESOLVED` | All its faults are cleared |
| `CLOSED` | Done: closed by an operator once resolved, or by itself 24 h later |

A rule can also open an incident on a pattern no single fault shows (twenty warnings in a minute,
say): it has no fault to resolve it, so an operator closes it, any time, with a note.

The incident page is a timeline: faults and recoveries as bars, then what rules, the agent,
operators and notifications did, on one time axis (scroll to zoom; click anything for its
detail). **Ask agent** asks about this incident; **Close** is there once it's resolved, with a
note that goes into the audit trail.

## 5. Recoveries and approvals

A recovery is a plan (restart a node, reset navigation, ...) that Jarvis runs through **gates**,
for rules, the agent and people alike: the recovery mode, who may ask for the plan, its
preconditions (never with the e-stop on, say), how often it may run, locks (one Nav2 reset at a
time), and approval. **Recovery** shows what runs (step by step), what waits for approval, and
what ran, with each gate's verdict.

- **Approve or reject**: a plan that needs approval waits in *Pending approval* (and on Overview,
  in the bell, on the agent's card if the agent asked). Read who asked and why (the rationale,
  the confidence for the agent), then approve with a note, or reject. After 5 minutes without a
  decision it expires. The gates are checked again after you approve. **Four-eyes**: a run you
  asked for needs someone else's approval (you may withdraw it); the dashboard shows "Another
  operator approves" instead of the button.
- **Run a plan yourself**: *Run plan…* lists the plans operators may run; fill in its parameters
  and a rationale. **Dry run** first when unsure: every gate runs, nothing moves, and you see what
  would have happened. A refused run says which gate refused it and why.
- **Cancel** stops a running plan (and the escalation steps after a failed one).
- When a plan fails, it may try the next one on its ladder (a restart, then a reset, then a safe
  stop) and then **escalates**: the incident goes to `ESCALATED`, and the shipped rule
  `escalation-notify` tells the `operators` channel (Slack, a pager: wherever your team pointed it).

## 6. The kill switch

The recovery mode, in the header (admins change it, with a reason):

| Mode | Jarvis |
|------|--------|
| `ENABLED` | recovers by itself, within the gates |
| `DRY_RUN` | decides everything and moves nothing: every request runs as a dry run |
| `DISABLED` | refuses every recovery, and drops what waits for approval or in a queue |

Switching to `DISABLED` from the dashboard lets running plans finish; to stop them too:
`ros2 jarvis mode DISABLED --cancel-running --reason "..."`. The mode survives restarts. The kill
switch stops *Jarvis'* recoveries; it doesn't stop the robot: that's the e-stop.

## 7. Rules

**Rules** (admins) shows what each rule did over the last day: fires, dry-run ("shadow") fires,
suppressed fires (it hit its rate limit, which usually means a human is needed), guard blocks and
failed actions. A rule can be switched off, or into **dry run** (it decides without acting:
useful to watch a new rule before trusting it). *Validate YAML…* checks a rule before it goes
in; *Reload from disk* loads the rules files again. Every change is recorded with your name.

**Rule lab** (admins; *Rules → Rule lab*, or *Try changes in the rule lab* on a rule) answers
"what would this rule have done?" before it goes live. Write a rule, or change one, choose how far
back (the last hour to 7 days) and *Backtest*: the robot's recorded events are replayed through the
same rule engine, with its other rules and its plans in dry run. You see:

- how often it would have fired, and how often its rate limit, cooldown or guard held it back;
- when, on a timeline, and on which event (a click opens it);
- the recoveries it would have asked for, and what the gates and the plan would have made of
  them;
- for a rule the robot already runs, every decision that differs from what it decided live.
  Unchanged, a rule makes the same decisions, event for event: that is how you know the replay
  can be trusted.

Nothing changes on the robot. To deploy the rule, put it in the robot's rules directory
(`rules:=`) and *Reload from disk*. The draft is checked like *Validate YAML…* first: a draft
with errors isn't replayed. Your last draft stays in this browser.

## 8. Asking the agent

The agent analyses incidents at ERROR or worse and failed recoveries by itself, and posts an
**insight**: what most likely happened (hypotheses, each with the events that show it: click an
evidence chip), how confident it is, and what to do. A recommended plan has a **Run** button: you
run it, as yourself, through the gates.

**Agent** → ask a question, about an incident (from the incident page, *Ask agent*) or about the
last minutes. It shows what it is doing, then its insight. Depending on how your team set it up,
the agent may also *propose* recoveries: they wait for your approval on its card like any other.
Without a model configured, the analyst is deterministic: it reasons from the events only.

## 9. Recordings

Jarvis records its event stream (with `/diagnostics` and `/rosout`) continuously, and keeps a
**black box** of the robot's sensors that it saves when a serious fault confirms. **System →
Recordings** lists both, with the incidents each file covers; closed files download as MCAP. Open
them in [Foxglove](https://foxglove.dev) to see what the robot saw. Recordings are kept a week by
default (the history itself, in the lake, much longer).

## 10. What was done, by whom

Every acknowledgement, clear, closed incident, mode change, rule change, approval, rejection,
cancellation and plan run by a person is an event with their name: **System → Audit trail**, the
*Operator* lane of an incident's timeline, or **Events** filtered on kind `OPERATOR`. Names come
from your token or your sign-in; from the command line, from `--operator` (or `$JARVIS_OPERATOR`).

## 11. From the command line

On the robot (or any machine on its ROS network):

```bash
ros2 jarvis faults                              # open faults (--all: with cleared and muted)
ros2 jarvis executions --steps                  # recoveries running, waiting, just finished
ros2 jarvis approve <execution-id> --note "checked the area"
ros2 jarvis reject <execution-id> --note "not now: maintenance"
ros2 jarvis cancel <execution-id> --reason "..."
ros2 jarvis recover restart-lifecycle-node -a node=/lidar_driver -a component=lidar --dry-run
ros2 jarvis mode                                # the recovery mode; DISABLED --reason "..." to stop
ros2 jarvis rules                               # the rules and what they did
ros2 jarvis rules dry-run <rule-id> [--off]     # shadow mode on (or off)
```

Acknowledging and clearing faults and closing incidents are in the dashboard and the API, not
in `ros2 jarvis`. The API is documented on the robot at `/docs`; scripts use a token
(`Authorization: Bearer ...`). On the command line, the ROS verbs don't check roles: anyone on
the robot's ROS network can use them, which is what the [security guide](security.md)'s SROS2
profile is for.
