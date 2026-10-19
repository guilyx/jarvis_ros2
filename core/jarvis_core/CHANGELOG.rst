^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Changelog for package jarvis_core
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Forthcoming
-----------
* First release: the ROS-agnostic core (C++20).
* Event model and validation; the event hub: sequencing, rate limits, flood control and load shedding.
* Fault manager: debounce, heal, latch, correlation (root cause and muted symptoms), incidents, freeze frames, black-box requests; state that survives restarts.
* Rule engine: event, count, sequence (with absence), all and any patterns; partitions, guards, cooldowns, rate limits, shadow mode; recover, emit, incident, notify and ask_agent actions.
* Recovery orchestrator: plans with parameters, preconditions, locks and queues, approvals (four-eyes: an operator may not approve their own request), retries, verification steps and escalation ladders, behind seven gates and a kill switch.
* The ``jarvis`` tool: ``lint`` (rules, plans and the fault catalog, cross-checked; it knows ``service.call`` and ``action.goal``) and ``backtest`` (replay a recording through the engine).
* ``jarvis-bench``: throughput and latency of the core against the requirements.
