# A recovery plan as loaded by the orchestrator (ListPlans): enough to show it and to fill its form.
string id
string description
string rev                           # content hash of the definition
string file
string params_json                   # {name: {type, required, default, pattern, one_of, description}}
string[] allowed_requesters          # OPERATOR, RULE, AGENT, PLAN
string[] requires_approval
string agent_policy                  # never, advise, supervised, autonomous
string[] steps                       # step ids, in order
float64 timeout_s
