# Securing Jarvis

Out of the box Jarvis trusts its machine: the ROS graph is open to anyone on the network who
shares the domain, and the gateway listens on `127.0.0.1` only. This guide closes it up for a real
deployment, one layer at a time:

1. [The ROS graph: SROS2](#1-the-ros-graph-sros2): every participant authenticated, every message
   encrypted, every topic and service limited to the processes that need it.
2. [The gateway: TLS and single sign-on](#2-the-gateway-tls-and-single-sign-on): HTTPS, and people
   signing in with your identity provider (OIDC) instead of shared tokens.
3. [The agent: what reaches the LLM](#3-the-agent-what-reaches-the-llm): redaction before any
   model call, and keys kept out of files.

Each layer is tested in CI: `ros/jarvis_bringup/test/test_security.py` runs the whole stack with
security enforced; `gateway/jarvis_gateway/test/test_auth.py` covers TLS and the proxy's identity;
the agent's tests check what a model is sent.

## 1. The ROS graph: SROS2

[`security/policy.xml`](../../ros/jarvis_bringup/security/policy.xml) gives each Jarvis process an
enclave of its own, with what it uses and nothing else:

| Enclave | Process | May |
|---------|---------|-----|
| `/jarvis/container` | the core: event hub, fault manager, rules, recovery, bridges, recorder, black box | publish `/jarvis/**` (the stream, faults, executions), serve Jarvis' services and actions, read any topic and call any service or action (it records, bridges and watches the robot, and recoveries act on it) |
| `/jarvis/lake` | `jarvis-lake` | publish `/jarvis/ingest` and diagnostics; read `/jarvis/faults` and closed splits |
| `/jarvis/gateway` | `jarvis-gateway` | read the stream; call the commands operators make (ack, clear, recover, modes, rules) and the agent |
| `/jarvis/agent` | `jarvis-agent` | publish insights on `/jarvis/ingest`; serve `analyze`; ask for recoveries |
| `/jarvis/launch` | `ros2 launch` itself | load the components into the container |
| `/jarvis/operator` | people: `ros2 jarvis`, the monitor, `ros2 topic ...` | read the stream and diagnostics; call Jarvis' services and actions |
| `/jarvis/producer` | a robot node reporting through `jarvis_reporter` | publish `/jarvis/ingest` (or `/jarvis/ingest_batches`), nothing else of Jarvis' |

What that buys: only the core can publish the stream (`/jarvis/event_batches`, `/jarvis/events`), `/jarvis/faults` and
`/jarvis/executions` or answer as a Jarvis service, so no other process can forge history, fake a
recovery or clear a fault. A node outside the keystore cannot even join the graph.

### Set it up

```bash
# The keystore: a CA, then one enclave per Jarvis process. It prints what to export.
ros2 run jarvis_bringup jarvis_keystore ~/jarvis_keystore
export ROS_SECURITY_KEYSTORE=~/jarvis_keystore
export ROS_SECURITY_ENABLE=true
export ROS_SECURITY_STRATEGY=Enforce

# ros2 launch runs as /jarvis/launch; each process it starts takes its own enclave.
ROS_SECURITY_ENCLAVE_OVERRIDE=/jarvis/launch ros2 launch jarvis_bringup jarvis.launch.py

# People, from another terminal:
ROS_SECURITY_ENCLAVE_OVERRIDE=/jarvis/operator ros2 jarvis faults
```

- **Your robot's nodes** need enclaves too: under `Enforce`, a participant without one cannot
  join. Write a policy for them (start from the `/jarvis/producer` profile for nodes that report
  to Jarvis, and add what they publish, subscribe and serve), then pass it along:
  `jarvis_keystore ~/jarvis_keystore robot_policy.xml`. `ros2 security generate_policy` writes one
  from a running graph, a good starting point.
- **The domain** is written into the permissions when they are generated (`$ROS_DOMAIN_ID` at the
  time): generate the keystore with the domain the robot runs on.
- **Enclave names**: the launch file gives Jarvis' processes `<enclave_root>/<process>`,
  `enclave_root:=/jarvis` by default. To nest them (`/robot1/jarvis/...`), change both the policy's
  paths and `enclave_root`.
- **The DDS**: Fast DDS (the default) and Cyclone DDS both enforce the policy. Fast DDS must be built
  with security (the ROS apt packages are; some other distributions are not, and then every node
  fails with "doesn't have the security libraries").
- **Keys are secrets**: each enclave's `key.pem` is that process' identity, and the keystore's
  `private/` holds the CA's keys. Copy only the enclaves a machine runs, and keep the CA offline.

## 2. The gateway: TLS and single sign-on

The gateway listens on `127.0.0.1:8080` by default: this machine only. To reach it from the LAN,
either give it TLS and bind a LAN address, or keep it local and put an authenticating proxy in
front (both, for the belt and the braces).

### TLS

```yaml
# gateway.yaml (gateway_config:=...)
schema: jarvis.gateway/v1
host: 0.0.0.0
port: 8443
tls: { cert: /etc/jarvis/tls.crt, key: /etc/jarvis/tls.key }
```

Or `jarvis-gateway --tls-cert ... --tls-key ...`. The dashboard and the live stream then work over
`https://` and `wss://`. Use your organization's CA, or Let's Encrypt for a robot with a public
name; `openssl req -x509 ...` for a test.

### Single sign-on (OIDC)

Signing people in is the proxy's job: [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/),
Pomerium or any reverse proxy that speaks your identity provider (Google, Microsoft Entra, Okta,
Keycloak, GitLab ...). It signs a person in and forwards who they are in headers; the gateway turns
their groups into roles:

```yaml
# gateway.yaml: the gateway stays on 127.0.0.1, the proxy is the way in
schema: jarvis.gateway/v1
host: 127.0.0.1
anonymous: null                    # no one without a token or a sign-in
proxy_auth:
  trusted: [127.0.0.1]             # the proxy's address: its headers are believed from there only
  user_header: X-Forwarded-Email
  groups_header: X-Forwarded-Groups
  roles: { jarvis-admins: admin, jarvis-operators: operator }    # the highest wins
  default_role: viewer             # signed in, in no listed group (null: refused)
```

```bash
oauth2-proxy \
  --provider=oidc --oidc-issuer-url=https://login.example.com \
  --client-id=jarvis --client-secret-file=/etc/jarvis/oidc-secret \
  --cookie-secret-file=/etc/jarvis/cookie-secret --email-domain=example.com \
  --upstream=http://127.0.0.1:8080 --http-address=0.0.0.0:4180 \
  --pass-user-headers=true --oidc-groups-claim=groups \
  --tls-cert-file=/etc/jarvis/tls.crt --tls-key-file=/etc/jarvis/tls.key
```

- **Trust**: the identity headers count only from `proxy_auth.trusted`. From anyone else they are
  ignored, or anyone could send `X-Forwarded-Email: admin@...` themselves. Keep the gateway bound
  to `127.0.0.1` behind the proxy, so nothing reaches it any other way.
- **Attribution**: what a signed-in person does (ack, clear, recover, approve) is recorded in the
  audit trail under their e-mail. The same name decides **four-eyes**: nobody approves a run they
  asked for (`separate_approver` on the recovery component, on by default).
- **AI assistants** get a token of their own, read-only unless you say otherwise
  ([AI assistants guide](ai-assistants.md#5-safety)).
- **Scripts and CI** keep using tokens (`Authorization: Bearer ...`), which take precedence over
  the proxy's headers. Keep tokens out of files: `$JARVIS_GATEWAY_TOKENS="token:name:role,..."`.

## 3. The agent: what reaches the LLM

Without a model nothing leaves the robot: the heuristic analyst runs on the agent's machine. With
one, every analysis sends the incident's events to the provider (or to your own server: Ollama,
vLLM, a LiteLLM proxy on the LAN). What it may see is up to `agent.yaml`:

```yaml
redact:
  - "attrs.gps.*"       # attributes, by glob: the robot's location
  - "attrs.customer*"
  - "source.host"       # a key of any mapping field
  - "payload"           # a whole field: the event's typed payload
  - "@identities"       # who did what: operators, approvers, requesters
scrub_emails: true      # e-mail addresses in any text (the default)
```

- **Where**: the patterns apply where the agent reads (its query port), so the prompt, every tool
  result, the triage and the windows all see the same redacted records: events, faults (and their
  freeze frames, which copy the attributes), recoveries, incidents and rules. A redacted value
  reads `[redacted]`; a mapping keeps its keys, so the model knows something was there.
- **What stays**: what an analysis runs on is never redacted, whatever the patterns: the record's
  structure (ids, `seq`, kind, code, severity, component, stamps, states, outcomes) and the
  attributes Jarvis itself writes (`fault_id`, `state`, `phase`, `plan` ...). `attrs.*` hides every
  attribute of your robot's and none of Jarvis'. Naming one of those (`seq`, `attrs.fault_id`) is
  refused when the agent starts, as is a pattern that is not a string, or an unknown `@macro`.
- **Messages and payloads** are free text: a pattern hides them whole or not at all. Keep what
  must not leave the robot out of messages, in attributes a pattern can name.
- **The last line**: right before each model call, every message is scrubbed of secrets, whatever
  the patterns: private keys, API keys (OpenAI, Anthropic, OpenRouter, AWS, GitHub, Slack,
  Google), JWTs, `Bearer`/`Basic` credentials, `password=`/`token:`/`api_key` values, the values
  of the agent's own secret variables (`$*_API_KEY`, `$*_TOKEN`, `$*_SECRET`, `$*_PASSWORD`), and
  e-mail addresses. The same goes for what the agent writes about a call: traces, progress,
  errors (a provider's error can echo the request).
- **Ids** the model passes to its tools must be ids (letters, digits, `.`, `_`, `:`, `@`, `-`):
  it cannot reach another route of the gateway through one.
- **API keys** come from the environment (`model.api_key_env`, by default the provider's own
  variable such as `$OPENROUTER_API_KEY`) or a file (`$JARVIS_AGENT_LLM_KEY_FILE`): a key written
  in `agent.yaml` is refused. So is a gateway token (`$JARVIS_AGENT_TOKEN`), which the agent sends
  in a header, never in a URL.
- **Logs**: the agent logs the incident a question is about, not the question; LiteLLM, the
  OpenAI client and the HTTP libraries are held at WARNING, below which they log requests. The
  gateway masks `?token=` (how the dashboard's live stream authenticates) in its access log.
- **Traces** (`traces_dir`) hold no event content: hashes, tool names and (scrubbed) arguments,
  tokens, cost, latency.

`agent/jarvis_agent/test/test_redaction.py` plants a location, an operator, a secret and an
address in the recorded demo tour, has a model call every tool on it, and checks that none of them
was sent.
