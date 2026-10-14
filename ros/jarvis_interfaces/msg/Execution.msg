# One recovery execution (docs/design/03-data-model.md §7). Canonical contract:
# schemas/execution.schema.json.

uint8 STATE_PENDING_APPROVAL=1
uint8 STATE_QUEUED=2
uint8 STATE_RUNNING=3
uint8 STATE_SUCCEEDED=4
uint8 STATE_FAILED=5
uint8 STATE_ABORTED=6
uint8 STATE_REJECTED=7
uint8 STATE_EXPIRED=8

# Same values as ExecuteRecovery.REQUESTER_*.
uint8 REQUESTER_OPERATOR=1
uint8 REQUESTER_RULE=2
uint8 REQUESTER_AGENT=3
uint8 REQUESTER_PLAN=4

string execution_id
string plan_id
string plan_rev                      # content hash of the plan it ran
Attribute[] args                     # bound: validated, defaults filled in
uint8 requester_type
string requester_id                  # rule id, operator name, insight id, parent execution id
string correlation_id                # incident
string causation_id
string rationale
float64 confidence                   # agent proposals; NaN when not given
bool dry_run
uint8 state
string reason                        # why it ended, or what it waits for
GateResult[] gates
bool needs_approval
string approved_by
string[] locks
StepProgress[] steps
uint16 current_step
string failed_step
builtin_interfaces/Time created
builtin_interfaces/Time started
builtin_interfaces/Time finished
builtin_interfaces/Time expires      # PENDING_APPROVAL and QUEUED deadline
uint8 depth
string parent_id                     # the execution whose `run` step started this one
string ladder_of                     # the failed execution whose on_failure started this one
bool ladder_running                  # FAILED, and its on_failure ladder is still in progress
bool escalated
