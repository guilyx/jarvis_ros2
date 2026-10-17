# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""One participant of test_security.py, in a process of its own: a robot node or a person, each in
their enclave (one process, one set of permissions, as on a robot). Prints what happened as JSON.

    python3 security_participant.py ENCLAVE raise CODE      # publish a fault on /jarvis/ingest
    python3 security_participant.py ENCLAVE faults CODE     # the open faults with that code
    python3 security_participant.py ENCLAVE forge           # try to publish and read the stream
    python3 security_participant.py ENCLAVE join            # just join the graph
"""

import json
import sys
import time

import rclpy
from jarvis_interfaces.msg import Event
from jarvis_interfaces.srv import GetFaults
from rclpy.duration import Duration
from rclpy.qos import QoSProfile, ReliabilityPolicy

NAMES = {"/jarvis/producer": "producer_test", "/jarvis/operator": "ros2_jarvis_test"}


def attempt(fn):
    try:
        fn()
        return "allowed"
    except Exception as e:  # noqa: BLE001 - rclpy raises its RCLError when DDS security says no
        return f"denied: {str(e).splitlines()[0][:200]}"


def main(enclave, action, *args):
    out = {}
    try:
        rclpy.init(args=["--ros-args", "--enclave", enclave])
        node = rclpy.create_node(NAMES.get(enclave, "participant_test"))
    except Exception as e:  # noqa: BLE001 - no keys for the enclave: rcl refuses to start
        print(json.dumps({"join": f"denied: {str(e).splitlines()[0][:200]}"}))
        return
    out["join"] = "allowed"
    if action == "raise":
        reliable = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)
        ingest = node.create_publisher(Event, "/jarvis/ingest", reliable)
        deadline = time.monotonic() + 30
        while ingest.get_subscription_count() == 0 and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
        e = Event(kind=Event.KIND_FAULT_RAISE, code=args[0], severity=Event.SEVERITY_ERROR)
        e.source.node, e.source.component = node.get_fully_qualified_name(), "security"
        e.stamp = node.get_clock().now().to_msg()
        ingest.publish(e)
        out["matched"] = ingest.get_subscription_count() > 0
        out["acked"] = ingest.wait_for_all_acked(Duration(seconds=10))
    elif action == "faults":
        client = node.create_client(GetFaults, "/jarvis/fault_manager/get_faults")
        out["faults"] = None
        if client.wait_for_service(timeout_sec=10.0):
            future = client.call_async(GetFaults.Request(codes=[args[0]]))
            rclpy.spin_until_future_complete(node, future, timeout_sec=10.0)
            if future.result() is not None:
                out["faults"] = [f.code for f in future.result().faults]
    elif action == "forge":
        out["publish_events"] = attempt(lambda: node.create_publisher(Event, "/jarvis/events", 10))
        out["read_events"] = attempt(lambda: node.create_subscription(Event, "/jarvis/events", print, 10))
    node.destroy_node()
    rclpy.try_shutdown()
    print(json.dumps(out))


if __name__ == "__main__":
    main(*sys.argv[1:])
