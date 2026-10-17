# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The port to a live Jarvis over ROS 2: its topics for what it publishes, its services and the
recovery action for commands.

rclpy spins in a thread of its own, in a context of its own. What it receives is converted to the
schemas' JSON there and handed to the gateway's event loop; replies come back as asyncio futures.
"""

import asyncio
import contextlib
import math
import threading

import rclpy
from jarvis_cli import convert
from jarvis_interfaces.action import ExecuteRecovery
from jarvis_interfaces.msg import Event, ExecutionArray, FaultArray
from jarvis_interfaces.srv import (
    AcknowledgeFault,
    CancelExecution,
    ClearFault,
    CloseIncident,
    DecideApproval,
    ListPlans,
    ListRules,
    QueryEvents,
    ReloadConfig,
    SetRecoveryMode,
    SetRuleState,
)
from rclpy.action import ActionClient
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.signals import SignalHandlerOptions

from jarvis_gateway.port import CommandResult, JarvisPort

STREAM = QoSProfile(depth=1000, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.VOLATILE)
LATCHED = QoSProfile(
    depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL
)
WAIT_S = 2.0  # how long a command waits for a service to show up before it is "unavailable"

TRISTATE = {
    None: SetRuleState.Request.KEEP,
    True: SetRuleState.Request.SET_TRUE,
    False: SetRuleState.Request.SET_FALSE,
}
FEEDBACK_STATES = convert._names(ExecuteRecovery.Feedback, "STATE_")
OUTCOMES = convert._names(ExecuteRecovery.Result, "OUTCOME_")

SERVICES = {
    "query": (QueryEvents, "event_hub/query_events"),
    "ack": (AcknowledgeFault, "fault_manager/acknowledge_fault"),
    "clear": (ClearFault, "fault_manager/clear_fault"),
    "close": (CloseIncident, "fault_manager/close_incident"),
    "decide": (DecideApproval, "recovery/decide"),
    "cancel": (CancelExecution, "recovery/cancel"),
    "mode": (SetRecoveryMode, "recovery/set_mode"),
    "plans": (ListPlans, "recovery/list_plans"),
    "plans_reload": (ReloadConfig, "recovery/reload"),
    "rules": (ListRules, "rule_engine/list_rules"),
    "rule_state": (SetRuleState, "rule_engine/set_rule_state"),
    "rules_reload": (ReloadConfig, "rule_engine/reload"),
}


class RosPort(JarvisPort):
    def __init__(self, namespace="/jarvis", node_name="gateway"):
        self.ns = "/" + namespace.strip("/")
        self.node_name = node_name
        self._loop = None
        self._sink = None
        self._context = None
        self._node = None
        self._executor = None
        self._thread = None
        self._clients = {}
        self._action = None
        self._goals = {}  # goal id -> handle in flight: kept alive until its result

    # -- lifecycle ---------------------------------------------------------------------------------

    async def start(self, sink):
        self._loop = asyncio.get_running_loop()
        self._sink = sink
        self._context = rclpy.Context()
        # uvicorn handles SIGINT/SIGTERM and stops the port: rclpy must not shut down on its own.
        rclpy.init(context=self._context, signal_handler_options=SignalHandlerOptions.NO)
        n = self._node = rclpy.create_node(self.node_name, namespace=self.ns, context=self._context)
        n.create_subscription(Event, f"{self.ns}/events", self._on_event, STREAM)
        n.create_subscription(FaultArray, f"{self.ns}/faults", self._on_faults, LATCHED)
        n.create_subscription(ExecutionArray, f"{self.ns}/executions", self._on_executions, LATCHED)
        for key, (srv_type, name) in SERVICES.items():
            self._clients[key] = (srv_type, n.create_client(srv_type, f"{self.ns}/{name}"))
        self._action = ActionClient(n, ExecuteRecovery, f"{self.ns}/recovery/execute")
        self._executor = SingleThreadedExecutor(context=self._context)
        self._executor.add_node(n)
        self._thread = threading.Thread(target=self._spin, name="rclpy", daemon=True)
        self._thread.start()

    def _spin(self):
        with contextlib.suppress(ExternalShutdownException, KeyboardInterrupt):
            self._executor.spin()

    async def stop(self):
        if self._executor is None:
            return
        self._executor.shutdown(timeout_sec=2.0)
        self._thread.join(timeout=2.0)
        self._node.destroy_node()
        self._context.try_shutdown()
        self._executor = None

    @property
    def connected(self):
        return bool(self._clients) and self._clients["query"][1].service_is_ready()

    # -- what Jarvis publishes (on the rclpy thread) -----------------------------------------------

    def _post(self, fn, *args):
        with contextlib.suppress(RuntimeError):  # the loop is closing
            self._loop.call_soon_threadsafe(fn, *args)

    def _on_event(self, msg):
        self._post(self._sink.on_event, convert.event_to_dict(msg))

    def _on_faults(self, msg):
        self._post(self._sink.on_faults, [convert.fault_to_dict(f) for f in msg.faults])

    def _on_executions(self, msg):
        self._post(self._sink.on_executions, convert.executions_to_dict(msg))

    # -- calls -------------------------------------------------------------------------------------

    def _bridge(self, rfut):
        """An rclpy future as an asyncio future of the gateway's loop."""
        afut = self._loop.create_future()

        def settle():
            if afut.done():
                return
            if rfut.cancelled():
                afut.cancel()
            elif rfut.exception() is not None:
                afut.set_exception(rfut.exception())
            else:
                afut.set_result(rfut.result())

        rfut.add_done_callback(lambda _: self._post(settle))
        return afut

    async def _call(self, key, **fields):
        srv_type, client = self._clients[key]
        if not client.service_is_ready():
            ready = await asyncio.to_thread(client.wait_for_service, timeout_sec=WAIT_S)
            if not ready:
                raise ConnectionError(f"{client.srv_name} is not available (is Jarvis running?)")
        rfut = client.call_async(srv_type.Request(**fields))
        try:
            return await self._bridge(rfut)
        except asyncio.CancelledError:
            client.remove_pending_request(rfut)
            raise

    async def query_events(self, after_seq=0, limit=1000):
        reply = await self._call("query", after_seq=int(after_seq), limit=int(limit))
        return [convert.event_to_dict(e) for e in reply.events], reply.next_seq, reply.complete

    # -- faults and incidents ----------------------------------------------------------------------

    async def ack_fault(self, fault_id, operator, note=""):
        r = await self._call("ack", fault_id=fault_id, operator_name=operator, note=note)
        return CommandResult(r.success, r.message)

    async def clear_fault(self, fault_id, operator, reason="", skip_symptoms=False):
        r = await self._call(
            "clear", fault_id=fault_id, operator_name=operator, reason=reason, skip_symptoms=skip_symptoms
        )
        return CommandResult(r.success, r.message, {"cleared_symptoms": list(r.cleared_symptoms)})

    async def close_incident(self, incident_id, operator, note=""):
        r = await self._call("close", incident_id=incident_id, operator_name=operator, note=note)
        return CommandResult(r.success, r.message)

    # -- recovery ----------------------------------------------------------------------------------

    async def recover(self, plan_id, args, operator, rationale="", dry_run=False, correlation_id=""):
        if not self._action.server_is_ready():
            ready = await asyncio.to_thread(self._action.wait_for_server, timeout_sec=WAIT_S)
            if not ready:
                raise ConnectionError(f"{self.ns}/recovery/execute is not available (is Jarvis running?)")
        first = self._loop.create_future()  # the execution as soon as the orchestrator took it

        def resolve(value):
            if not first.done():
                first.set_result(value)

        def on_feedback(msg):
            f = msg.feedback
            self._post(resolve, (f.execution_id, FEEDBACK_STATES.get(f.state, "QUEUED"), f.message))

        goal = ExecuteRecovery.Goal(
            plan_id=plan_id,
            args=convert.make_attrs(args),
            requester_type=ExecuteRecovery.Goal.REQUESTER_OPERATOR,
            requester_id=operator,
            correlation_id=correlation_id,
            rationale=rationale,
            confidence=math.nan,
            dry_run=dry_run,
        )
        handle = await self._bridge(self._action.send_goal_async(goal, feedback_callback=on_feedback))
        if not handle.accepted:
            return CommandResult(False, "the recovery component is not active")
        key = bytes(handle.goal_id.uuid)
        self._goals[key] = handle

        def on_result(rfut):
            self._post(self._goals.pop, key, None)
            if rfut.exception() is None:
                r = rfut.result().result
                self._post(resolve, (r.execution_id, OUTCOMES.get(r.outcome, "ABORTED"), r.reason))

        handle.get_result_async().add_done_callback(on_result)
        execution_id, state, message = await first
        data = {"execution_id": execution_id, "state": state}
        return CommandResult(state != "REJECTED", message or state.lower(), data)

    async def decide(self, execution_id, approve, operator, note=""):
        r = await self._call(
            "decide", execution_id=execution_id, approve=approve, operator_name=operator, note=note
        )
        return CommandResult(r.success, r.message)

    async def cancel(self, execution_id, operator, reason=""):
        r = await self._call("cancel", execution_id=execution_id, operator_name=operator, reason=reason)
        return CommandResult(r.success, r.message)

    async def set_mode(self, mode, operator, reason="", cancel_running=False):
        r = await self._call(
            "mode", mode=mode, operator_name=operator, reason=reason, cancel_running=cancel_running
        )
        return CommandResult(r.success, r.message, {"previous_mode": r.previous_mode})

    async def list_plans(self):
        r = await self._call("plans")
        return [convert.plan_to_dict(p) for p in r.plans], r.mode

    # -- rules and configuration -------------------------------------------------------------------

    async def list_rules(self):
        r = await self._call("rules")
        return [convert.rule_to_dict(x) for x in r.rules]

    async def set_rule_state(self, rule_id, enabled, dry_run, operator, reason=""):
        r = await self._call(
            "rule_state",
            rule_id=rule_id,
            enabled=TRISTATE[enabled],
            dry_run=TRISTATE[dry_run],
            operator_name=operator,
            reason=reason,
        )
        return CommandResult(r.success, r.message)

    async def reload(self, target, validate_only, operator):
        key = {"rules": "rules_reload", "plans": "plans_reload"}[target]
        r = await self._call(key, validate_only=validate_only, operator_name=operator)
        message = r.message
        if r.errors:
            message = "; ".join([message, *r.errors[:5]]) if message else "; ".join(r.errors[:5])
        data = {"revision": r.revision, "errors": list(r.errors), "warnings": list(r.warnings)}
        return CommandResult(r.success, message, data)
