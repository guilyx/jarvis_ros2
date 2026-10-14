# The one record type Jarvis moves around. Canonical contract: schemas/event.schema.json.

uint8 KIND_UNSPECIFIED=0
uint8 KIND_LOG=1
uint8 KIND_STATE=2
uint8 KIND_FAULT_RAISE=3
uint8 KIND_FAULT_CLEAR=4
uint8 KIND_FAULT_STATE=5
uint8 KIND_RULE_MATCH=6
uint8 KIND_RECOVERY=7
uint8 KIND_INSIGHT=8
uint8 KIND_OPERATOR=9
uint8 KIND_SYSTEM=10

uint8 SEVERITY_DEBUG=10
uint8 SEVERITY_INFO=20
uint8 SEVERITY_WARN=30
uint8 SEVERITY_ERROR=40
uint8 SEVERITY_CRITICAL=50

string id                            # UUIDv7; the event hub fills it when empty
uint64 seq                           # 0 on /jarvis/ingest; assigned by the event hub
builtin_interfaces/Time stamp        # when it happened (producer clock)
builtin_interfaces/Time recv_stamp   # when the event hub ingested it
Source source
uint8 kind
string code                          # dotted lowercase, e.g. ros.topic.stale
uint8 severity
string message
Attribute[] attrs
string[] tags
string payload                       # optional JSON document
string payload_schema                # e.g. jarvis.insight/v1
string correlation_id                # incident id
string causation_id                  # id of the event that caused this one
