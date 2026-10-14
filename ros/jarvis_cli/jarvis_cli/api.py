# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Small, blocking helpers over rclpy for one-shot CLI calls."""

import contextlib
import os
import shutil
import uuid

import rclpy
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

LATCHED = QoSProfile(
    depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL
)


class JarvisError(RuntimeError):
    """A failure to report to the user, without a traceback."""


@contextlib.contextmanager
def node(args=None):
    rclpy.init()
    n = rclpy.create_node(f"ros2_jarvis_{uuid.uuid4().hex[:8]}")
    try:
        yield n
    finally:
        n.destroy_node()
        rclpy.try_shutdown()


def call(n, srv_type, name, request, timeout=10.0):
    client = n.create_client(srv_type, name)
    try:
        if not client.wait_for_service(timeout_sec=timeout):
            raise JarvisError(f"{name} is not available (is Jarvis running?)")
        future = client.call_async(request)
        rclpy.spin_until_future_complete(n, future, timeout_sec=timeout)
        if not future.done():
            raise JarvisError(f"{name} did not answer within {timeout:.0f} s")
        return future.result()
    finally:
        n.destroy_client(client)


def latched(n, msg_type, topic, timeout=5.0):
    """The current sample of a latched topic, or None."""
    box = []
    sub = n.create_subscription(msg_type, topic, box.append, LATCHED)
    try:
        deadline = n.get_clock().now().nanoseconds + int(timeout * 1e9)
        while not box and n.get_clock().now().nanoseconds < deadline:
            rclpy.spin_once(n, timeout_sec=0.1)
        return box[-1] if box else None
    finally:
        n.destroy_subscription(sub)


def operator_name(given):
    return given or os.environ.get("JARVIS_OPERATOR") or os.environ.get("USER") or "cli"


def jarvis_binary():
    """The `jarvis` tool installed by jarvis_core (on PATH in a sourced workspace)."""
    found = os.environ.get("JARVIS_BIN") or shutil.which("jarvis")
    if not found:
        raise JarvisError("the `jarvis` tool is not on PATH (source the workspace, or set JARVIS_BIN)")
    return found


def run(verb):
    """Turns JarvisError and ValueError into a message and exit code 1."""

    def wrapper(self, *, args):
        try:
            return verb(self, args=args)
        except (JarvisError, ValueError) as e:
            print(f"error: {e}")
            return 1

    return wrapper
