# One fault episode. Canonical contract: schemas/fault.schema.json.

uint8 STATE_PENDING=1
uint8 STATE_ACTIVE=2
uint8 STATE_LATCHED=3
uint8 STATE_CLEARED=4

string fault_id
string code
Source source
string[] reporting_sources
uint8 state
uint8 severity                       # Event.SEVERITY_*
uint32 occurrences
builtin_interfaces/Time first_seen
builtin_interfaces/Time last_seen
builtin_interfaces/Time confirmed_at
builtin_interfaces/Time cleared_at
bool acknowledged
string acknowledged_by
bool latch
bool cataloged
string muted_by                      # fault_id of the root cause when this is a muted symptom
string incident_id
string snapshot_ref                  # black-box recording, when captured
string description
string message
Attribute[] freeze_frame
