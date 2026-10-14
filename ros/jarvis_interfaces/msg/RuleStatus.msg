# One loaded rule and its counters since the rule engine started.
string id
string description
string file
bool enabled
bool dry_run
uint64 fires
uint64 dry_fires
uint64 suppressed                    # max_fires reached
uint64 cooled_down
uint64 guard_blocked
uint64 action_failures
uint32 partitions
