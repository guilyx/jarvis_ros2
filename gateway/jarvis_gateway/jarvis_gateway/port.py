# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""What the gateway needs from Jarvis, whatever carries it: the ROS graph (adapters/ros2.py) or a
fake for tests and dashboard work without a robot (adapters/fake.py).

The gateway never writes data itself: every command goes to the Jarvis component that owns it, which
records who asked on the event stream (the audit trail).
"""

import abc
import dataclasses


@dataclasses.dataclass
class CommandResult:
    success: bool
    message: str = ""
    data: dict = dataclasses.field(default_factory=dict)


class Sink(abc.ABC):
    """Where a port delivers what it receives (called on the gateway's event loop)."""

    @abc.abstractmethod
    def on_event(self, event): ...

    @abc.abstractmethod
    def on_faults(self, faults): ...

    @abc.abstractmethod
    def on_executions(self, snapshot): ...


class JarvisPort(abc.ABC):
    @abc.abstractmethod
    async def start(self, sink):
        """Starts receiving: events, the faults snapshot and the executions snapshot go to `sink`."""

    @abc.abstractmethod
    async def stop(self): ...

    @property
    @abc.abstractmethod
    def connected(self):
        """True once Jarvis answered (its snapshots arrived)."""

    @abc.abstractmethod
    async def query_events(self, after_seq=0, limit=1000):
        """The event hub's recent events after `after_seq`: (events, next_seq, complete)."""

    # -- faults and incidents (fault manager) ------------------------------------------------------

    @abc.abstractmethod
    async def ack_fault(self, fault_id, operator, note=""): ...

    @abc.abstractmethod
    async def clear_fault(self, fault_id, operator, reason="", skip_symptoms=False): ...

    @abc.abstractmethod
    async def close_incident(self, incident_id, operator, note=""): ...

    # -- recovery (orchestrator) -------------------------------------------------------------------

    @abc.abstractmethod
    async def recover(self, plan_id, args, operator, rationale="", dry_run=False, correlation_id=""):
        """Asks for a plan as an operator. data: {execution_id, state} once the orchestrator took it."""

    @abc.abstractmethod
    async def decide(self, execution_id, approve, operator, note=""): ...

    @abc.abstractmethod
    async def cancel(self, execution_id, operator, reason=""): ...

    @abc.abstractmethod
    async def set_mode(self, mode, operator, reason="", cancel_running=False): ...

    @abc.abstractmethod
    async def list_plans(self):
        """(plans, mode): plans as dicts (id, description, params, steps, ...)."""

    # -- rules (rule engine) and configuration -----------------------------------------------------

    @abc.abstractmethod
    async def list_rules(self): ...

    @abc.abstractmethod
    async def set_rule_state(self, rule_id, enabled, dry_run, operator, reason=""): ...

    @abc.abstractmethod
    async def reload(self, target, validate_only, operator):
        """target: "rules" or "plans". data: {revision, errors, warnings}."""
