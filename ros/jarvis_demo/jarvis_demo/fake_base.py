# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Stands in for the robot's base in the docking demo: its battery, Nav2's docking server and AMCL.

    /battery_state      sensor_msgs/BatteryState, and `power.battery.low` (WARN) through
                        jarvis_reporter while the battery is low and not charging
    /dock_robot         nav2_msgs/action/DockRobot, as Nav2's docking server serves it: feedback
                        through its states, then the result (success, error_code, error_msg)
    /set_initial_pose   nav2_msgs/srv/SetInitialPose, as AMCL serves it

Jarvis drives the last two with the generic actuators (action.goal, service.call): the demo's
docking plan names their types, nothing here is Jarvis-specific. Faults are injected at runtime:

    ros2 param set /demo/base inject drain     # the battery drops to 12 %  -> power.battery.low
    ros2 param set /demo/base inject no_dock   # the dock is not detected   -> DockRobot aborts (904)
    ros2 param set /demo/base inject none
"""

import time

import rclpy
from jarvis_reporter import Reporter, Severity
from nav2_msgs.action import DockRobot
from nav2_msgs.srv import SetInitialPose
from rcl_interfaces.msg import ParameterDescriptor, SetParametersResult
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import BatteryState

INJECTIONS = ("none", "drain", "no_dock")
LOW_CODE = "power.battery.low"
F = DockRobot.Feedback
R = DockRobot.Result
STAGES = (  # what the docking server does, and for how long (s)
    (F.NAV_TO_STAGING_POSE, "driving to the staging pose", 2.0),
    (F.INITIAL_PERCEPTION, "looking for the dock", 1.0),
    (F.CONTROLLING, "backing onto the dock", 2.0),
    (F.WAIT_FOR_CHARGE, "waiting for the charge to start", 1.0),
)


class FakeBase(Node):
    def __init__(self):
        super().__init__("base")
        self.declare_parameter("battery_pct", 64.0)
        self.declare_parameter("low_pct", 15.0)
        self.declare_parameter("charge_rate_pct_s", 3.0)
        self.declare_parameter("docks", ["home"])
        self.declare_parameter("inject", "none", ParameterDescriptor(description=" | ".join(INJECTIONS)))
        self.add_on_set_parameters_callback(self._validate)
        self._pct = float(self.get_parameter("battery_pct").value)
        self._docked = ""  # the dock we are on
        self._jarvis = Reporter(self, "battery")
        group = ReentrantCallbackGroup()
        self._battery = self.create_publisher(BatteryState, "/battery_state", 10)
        ActionServer(
            self,
            DockRobot,
            "/dock_robot",
            self._dock,
            goal_callback=lambda _goal: GoalResponse.ACCEPT,
            cancel_callback=lambda _goal: CancelResponse.ACCEPT,
            callback_group=group,
        )
        self.create_service(SetInitialPose, "/set_initial_pose", self._initial_pose, callback_group=group)
        self.create_timer(1.0, self._tick)

    def _validate(self, params):
        for p in params:
            if p.name == "inject" and p.value not in INJECTIONS:
                return SetParametersResult(successful=False, reason=f"inject: one of {', '.join(INJECTIONS)}")
            if p.name == "inject" and p.value == "drain":
                self._pct, self._docked = 12.0, ""
                self.get_logger().warning("injected: the battery drops to 12 %, off the dock")
        return SetParametersResult(successful=True)

    def _tick(self):
        if self._docked:
            self._pct = min(100.0, self._pct + self.get_parameter("charge_rate_pct_s").value)
        msg = BatteryState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.percentage = self._pct / 100.0
        msg.power_supply_status = (
            BatteryState.POWER_SUPPLY_STATUS_CHARGING
            if self._docked
            else BatteryState.POWER_SUPPLY_STATUS_DISCHARGING
        )
        msg.present = True
        self._battery.publish(msg)
        self._jarvis.state("power.battery_pct", round(self._pct))
        self._jarvis.state("power.dock", "DOCKED" if self._docked else "UNDOCKED")
        if self._pct < self.get_parameter("low_pct").value and not self._docked:
            self._jarvis.fault(
                LOW_CODE,
                Severity.WARN,
                f"battery at {self._pct:.0f} %, not charging",
                {"pct": round(self._pct)},
            )
        elif self._docked:
            self._jarvis.clear(LOW_CODE, f"charging on dock {self._docked} ({self._pct:.0f} %)")
        else:
            self._jarvis.clear(LOW_CODE, f"battery at {self._pct:.0f} %")

    def _dock(self, goal_handle):
        goal = goal_handle.request
        log = self.get_logger()
        log.info(
            f"DockRobot: dock_id={goal.dock_id!r} navigate_to_staging_pose={goal.navigate_to_staging_pose}"
        )
        result = R()
        if goal.use_dock_id and goal.dock_id not in self.get_parameter("docks").value:
            result.success, result.error_code = False, R.DOCK_NOT_IN_DB
            result.error_msg = f"dock '{goal.dock_id}' is not in the dock database"
            log.error(f"DockRobot: {result.error_msg}")
            goal_handle.abort()
            return result
        started = time.monotonic()
        for state, what, seconds in STAGES:
            if not goal.navigate_to_staging_pose and state == F.NAV_TO_STAGING_POSE:
                continue
            log.info(f"DockRobot feedback: {what}")
            feedback = F()
            feedback.state = state
            elapsed = time.monotonic() - started
            feedback.docking_time.sec = int(elapsed)
            goal_handle.publish_feedback(feedback)
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if goal_handle.is_cancel_requested:
                    log.warning("DockRobot: canceled")
                    goal_handle.canceled()
                    return R(success=False, error_code=R.UNKNOWN, error_msg="canceled")
                time.sleep(0.05)
            if state == F.INITIAL_PERCEPTION and self.get_parameter("inject").value == "no_dock":
                result.success, result.error_code = False, R.FAILED_TO_DETECT_DOCK
                result.error_msg = f"dock '{goal.dock_id}' not detected"
                log.error(f"DockRobot: {result.error_msg}")
                goal_handle.abort()
                return result
        self._docked = goal.dock_id or "home"
        log.info(f"DockRobot: docked on {self._docked}, charging")
        goal_handle.succeed()
        result.success = True
        return result

    def _initial_pose(self, request, response):
        p = request.pose.pose.pose
        self.get_logger().info(
            f"SetInitialPose: localization re-seeded at ({p.position.x:.2f}, {p.position.y:.2f}) "
            f"in {request.pose.header.frame_id or '?'}"
        )
        return response


def main(args=None):
    rclpy.init(args=args)
    node = FakeBase()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
