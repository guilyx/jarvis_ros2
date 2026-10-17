# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The agent on the ROS graph, one node (/jarvis/agent):

- insights out: INSIGHT events on /jarvis/ingest (the event hub records them, the dashboard shows
  them, the lake keeps them);
- proposals in: ExecuteRecovery goals as requester AGENT, the one gate rules and operators use too.
  The orchestrator applies the deployment's agent autonomy, the plan's agent_policy and the
  confidence threshold; `submit` returns as soon as it took the goal (a supervised proposal waits
  for an operator long after);
- questions: the Analyze action (/jarvis/agent/analyze), which the gateway's /agent/ask calls;
  `serve` answers it with the agent, a few at a time, feedback as it goes.

rclpy is imported only here; the node has its own context and spins in its own thread.
"""

import contextlib
import json
import logging
import math
import threading

import rclpy
from jarvis_interfaces.action import Analyze, ExecuteRecovery
from jarvis_interfaces.msg import Attribute, Event
from rclpy.action import ActionClient, ActionServer
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.signals import SignalHandlerOptions

from jarvis_agent.core.ports import Submitted

log = logging.getLogger("jarvis_agent.ros")

FEEDBACK_STATES = {
    ExecuteRecovery.Feedback.STATE_PENDING_APPROVAL: "PENDING_APPROVAL",
    ExecuteRecovery.Feedback.STATE_QUEUED: "QUEUED",
    ExecuteRecovery.Feedback.STATE_RUNNING: "RUNNING",
}
OUTCOMES = {
    ExecuteRecovery.Result.OUTCOME_SUCCEEDED: "SUCCEEDED",
    ExecuteRecovery.Result.OUTCOME_FAILED: "FAILED",
    ExecuteRecovery.Result.OUTCOME_ABORTED: "ABORTED",
    ExecuteRecovery.Result.OUTCOME_REJECTED: "REJECTED",
    ExecuteRecovery.Result.OUTCOME_EXPIRED: "EXPIRED",
}


class RosAgent:
    def __init__(self, namespace="/jarvis", node_name="agent", domain_id=None):
        self.ns = ns = namespace.rstrip("/")
        self.context = rclpy.Context()
        rclpy.init(context=self.context, domain_id=domain_id, signal_handler_options=SignalHandlerOptions.NO)
        self.node = rclpy.create_node(node_name, namespace=namespace, context=self.context)
        qos = QoSProfile(
            depth=100, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.VOLATILE
        )
        self.publisher = self.node.create_publisher(Event, f"{ns}/ingest", qos)
        # Reentrant, on several threads: an analysis (Analyze) runs while feedback and the answers
        # to its proposals (ExecuteRecovery) come in.
        self._group = ReentrantCallbackGroup()
        self.action = ActionClient(
            self.node, ExecuteRecovery, f"{ns}/recovery/execute", callback_group=self._group
        )
        self._server = None
        self._goals = {}  # goal id -> handle, until its result (kept alive)
        self._lock = threading.Lock()
        self.executor = MultiThreadedExecutor(num_threads=4, context=self.context)
        self.executor.add_node(self.node)
        self._thread = threading.Thread(target=self._spin, name="jarvis-agent-ros", daemon=True)
        self._thread.start()

    def _spin(self):
        with contextlib.suppress(Exception):  # the executor stops when the node shuts down
            self.executor.spin()

    def publish(self, insight, correlation_id="", causation_id=""):
        e = Event(kind=Event.KIND_INSIGHT, code="jarvis.insight", severity=Event.SEVERITY_INFO)
        e.source.node = self.node.get_fully_qualified_name()
        e.message = insight.title
        e.payload = json.dumps(insight.to_json(), separators=(",", ":"))
        e.payload_schema = "jarvis.insight/v1"
        e.correlation_id = correlation_id
        e.causation_id = causation_id
        e.attrs = [
            Attribute(key="insight_id", type=Attribute.TYPE_STRING, string_value=insight.insight_id),
            Attribute(key="analyst", type=Attribute.TYPE_STRING, string_value=insight.analyst),
            Attribute(key="confidence", type=Attribute.TYPE_DOUBLE, double_value=float(insight.confidence)),
            Attribute(key="degraded", type=Attribute.TYPE_BOOL, bool_value=insight.degraded),
        ]
        self.publisher.publish(e)

    def submit(
        self, plan_id, args, rationale, confidence, correlation_id="", causation_id="", timeout_s=10.0
    ):
        if not self.action.wait_for_server(timeout_sec=timeout_s):
            return Submitted("", "UNAVAILABLE", "recovery/execute is not available (is Jarvis running?)")
        done, out = threading.Event(), {}

        def first(execution_id, state, reason):
            with self._lock:
                if not done.is_set():
                    out.update(execution_id=execution_id, state=state, reason=reason)
                    done.set()

        def on_feedback(msg):
            f = msg.feedback
            first(f.execution_id, FEEDBACK_STATES.get(f.state, "QUEUED"), f.message)

        def on_result(future, key):
            self._goals.pop(key, None)
            if future.exception() is None:
                r = future.result().result
                first(r.execution_id, OUTCOMES.get(r.outcome, "ABORTED"), r.reason)

        def on_goal(future):
            if future.exception() is not None:
                first("", "UNAVAILABLE", f"the goal was not sent: {future.exception()}")
                return
            handle = future.result()
            if not handle.accepted:
                first("", "REJECTED", "the recovery component is not active")
                return
            key = bytes(handle.goal_id.uuid)
            self._goals[key] = handle
            handle.get_result_async().add_done_callback(lambda f: on_result(f, key))

        goal = ExecuteRecovery.Goal(
            plan_id=plan_id,
            args=[Attribute(key=k, type=Attribute.TYPE_STRING, string_value=v) for k, v in args.items()],
            requester_type=ExecuteRecovery.Goal.REQUESTER_AGENT,
            requester_id=self.node.get_fully_qualified_name(),
            correlation_id=correlation_id,
            causation_id=causation_id,
            rationale=rationale,
            confidence=float(confidence) if confidence is not None else math.nan,
            dry_run=False,
        )
        self.action.send_goal_async(goal, feedback_callback=on_feedback).add_done_callback(on_goal)
        if not done.wait(timeout_s):
            return Submitted("", "UNKNOWN", f"no answer from the orchestrator within {timeout_s:g} s")
        return Submitted(**out)

    def serve(self, ask, concurrency=2):
        """Answers /jarvis/agent/analyze with ask(incident_id, question, progress) -> Insight
        (LookupError or ValueError: a refusal), `concurrency` questions at a time."""
        slots = threading.BoundedSemaphore(concurrency)

        def execute(handle):
            g = handle.request
            if not slots.acquire(blocking=False):
                handle.abort()
                return Analyze.Result(success=False, message="the agent is busy: ask again in a moment")
            try:
                # The incident, not the question: what people type is not for the logs.
                log.info("asked by %s about %s", g.requester or "?", g.incident_id or "the recent history")

                def progress(stage, detail=""):
                    handle.publish_feedback(Analyze.Feedback(stage=stage, detail=str(detail)[:500]))

                try:
                    insight = ask(g.incident_id, g.question, progress)
                except (LookupError, ValueError) as e:
                    handle.abort()
                    return Analyze.Result(success=False, message=str(e.args[0] if e.args else e))
                except Exception as e:  # noqa: BLE001 - the asker hears it, the agent goes on
                    log.exception("the analysis for %s failed", g.requester)
                    handle.abort()
                    return Analyze.Result(success=False, message=f"the analysis failed: {type(e).__name__}")
                handle.succeed()
                return Analyze.Result(
                    success=True, insight_json=json.dumps(insight.to_json(), separators=(",", ":"))
                )
            finally:
                slots.release()

        self._server = ActionServer(
            self.node, Analyze, f"{self.ns}/agent/analyze", execute, callback_group=self._group
        )

    def close(self):
        if self._server is not None:
            self._server.destroy()
        self.executor.shutdown(timeout_sec=1.0)
        self.node.destroy_node()
        self.context.try_shutdown()
        self._thread.join(timeout=2.0)


RosInsightSink = RosAgent  # its first name (4.1)
