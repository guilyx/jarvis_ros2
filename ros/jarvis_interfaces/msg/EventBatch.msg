# Events in order, one message per group: /jarvis/event_batches (from the event hub) and
# /jarvis/ingest_batches (from high-rate producers). One message per event costs more in the
# middleware than Jarvis spends on the event; a batch pays it once (docs/design/04-data-flow.md §1).
Event[] events
