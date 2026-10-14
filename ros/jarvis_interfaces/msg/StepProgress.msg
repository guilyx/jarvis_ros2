# One step of a recovery execution.
uint8 STATE_PENDING=1
uint8 STATE_RUNNING=2
uint8 STATE_RETRYING=3
uint8 STATE_SUCCEEDED=4
uint8 STATE_FAILED=5
uint8 STATE_SKIPPED=6
uint8 STATE_ABORTED=7

string id
string action
uint8 state
uint16 attempts
builtin_interfaces/Time started
builtin_interfaces/Time finished
string message
