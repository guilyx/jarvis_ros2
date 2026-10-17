# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""A crash (milestone 5.3, NFR-6): the whole stack is killed with SIGKILL while events flow, then
started again on the same data directory. What must survive: the open faults (fsynced snapshots),
the stream's sequence (the boot counter), and the recorded events up to the last moments before the
crash (the torn split is recovered into the lake).

A kill keeps what the OS had not written yet; a power cut would not. The state files are fsynced
for that; recordings lose the MCAP chunk the recorder had not written yet (config/mcap_writer.yaml)."""

import os
import signal
import subprocess
import time

import pytest
import rclpy
from jarvis_interfaces.msg import Event
from jarvis_interfaces.srv import GetFaults
from rclpy.qos import QoSProfile, ReliabilityPolicy

RATE = 200  # events per second, from 8 components: under the hub's flood control
SECONDS = 6
MAX_LOST_S = 1.5  # NFR-6 is ~1 s; a shared CI machine gets some slack
FAULTS = ("test.durability.a", "test.durability.b", "test.durability.c")


def launch(data_dir, log):
    """The bringup in its own process group, so the crash takes all of it."""
    return subprocess.Popen(
        [
            "ros2", "launch", "jarvis_bringup", "jarvis.launch.py", f"data_dir:={data_dir}",
            "robot_id:=durable-bot", "gateway:=false", "agent:=false", "bridges:=false",
        ],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )  # fmt: skip


def crash(proc):
    os.killpg(proc.pid, signal.SIGKILL)
    proc.wait(timeout=10)


def stop(proc):
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGINT)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)


@pytest.fixture
def ros():
    rclpy.init()
    node = rclpy.create_node("durability_test")
    yield node
    node.destroy_node()
    rclpy.shutdown()


def wait_for(node, predicate, what, timeout=60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        rclpy.spin_once(node, timeout_sec=0.1)
    pytest.fail(f"timed out waiting for {what}")


def recording(node):
    """The recorder is subscribed to the event stream (it starts last)."""
    subs = node.get_subscriptions_info_by_topic("/jarvis/events")
    return any(s.node_name == "recorder" for s in subs)


def open_faults(node, client):
    if not client.wait_for_service(timeout_sec=1.0):
        return None
    future = client.call_async(GetFaults.Request(codes=["test.durability.*"]))
    rclpy.spin_until_future_complete(node, future, timeout_sec=5.0)
    return future.result().faults if future.result() else None


def test_a_crash_keeps_faults_sequence_and_recordings(ros, tmp_path):
    data_dir = tmp_path
    log = open(data_dir / "launch.log", "w")  # noqa: SIM115 - both runs write to it
    reliable = QoSProfile(depth=1000, reliability=ReliabilityPolicy.RELIABLE)
    ingest = ros.create_publisher(Event, "/jarvis/ingest", reliable)
    seqs, published = [], {}  # the stream as the hub published it: tick id -> when it was seen

    def on_event(e):
        seqs.append(e.seq)
        if e.code == "test.durability.tick":
            published[e.id] = time.monotonic()

    ros.create_subscription(Event, "/jarvis/events", on_event, reliable)
    faults = ros.create_client(GetFaults, "/jarvis/fault_manager/get_faults")

    first = launch(data_dir, log)
    try:
        wait_for(ros, lambda: ingest.get_subscription_count() > 0, "the event hub", 90)
        wait_for(ros, lambda: open_faults(ros, faults) is not None, "the fault manager", 60)
        wait_for(ros, lambda: recording(ros), "the recorder", 60)
        for code in FAULTS:
            e = Event(kind=Event.KIND_FAULT_RAISE, code=code, severity=Event.SEVERITY_ERROR)
            e.source.node, e.source.component = "/durability", "durability"
            e.stamp = ros.get_clock().now().to_msg()
            ingest.publish(e)
        wait_for(ros, lambda: len(open_faults(ros, faults) or []) == 3, "the three faults", 30)

        # Events flow, then the crash: nothing gets to shut down cleanly.
        sent, start = 0, time.monotonic()
        while time.monotonic() - start < SECONDS:
            e = Event(kind=Event.KIND_LOG, code="test.durability.tick", severity=Event.SEVERITY_INFO)
            e.id = f"tick-{sent}"
            e.source.node, e.source.component = "/durability", f"durability-{sent % 8}"
            e.stamp = ros.get_clock().now().to_msg()
            ingest.publish(e)
            sent += 1
            while (wait := start + sent / RATE - time.monotonic()) > 0:
                rclpy.spin_once(ros, timeout_sec=wait)
        last_seq = max(seqs)
        crashed_at = time.monotonic()
        crash(first)
    except BaseException:
        stop(first)
        raise

    second = launch(data_dir, log)
    try:
        wait_for(ros, lambda: open_faults(ros, faults) is not None, "the fault manager again", 90)
        restored = {f.code for f in open_faults(ros, faults)}
        assert restored == set(FAULTS), f"faults after the crash: {restored}"

        # The boot counter moved on: seqs of the new run are above every seq of the first.
        seqs.clear()
        e = Event(kind=Event.KIND_LOG, code="test.durability.after", severity=Event.SEVERITY_INFO)
        e.source.node = "/durability"
        ingest.publish(e)
        wait_for(ros, lambda: seqs, "an event after the crash", 30)
        assert min(seqs) > last_seq

        # The torn split of the first run is recovered into the lake: the stream up to the crash, bar
        # its last moments (the chunk the recorder had not written yet).
        from jarvis_lake.lake import Lake

        def recovered():
            try:
                con = Lake(str(data_dir)).connect()
                rows = con.execute("SELECT id FROM lake_events WHERE code = 'test.durability.tick'")
                return {r[0] for r in rows.fetchall()}
            except Exception:  # noqa: BLE001 - nothing converted yet
                return set()

        wait_for(ros, lambda: recovered(), "the recovered split in the lake", 60)
        time.sleep(2.0)
        kept = recovered()
        missing = [seen for id_, seen in published.items() if id_ not in kept]
        lost_s = crashed_at - min(missing, default=crashed_at)
        print(f"durability: {len(missing)} of {len(published)} events lost, the last {lost_s:.2f} s")
        assert len(published) > RATE * SECONDS * 0.9, f"{len(published)} of {sent} published"
        assert lost_s <= MAX_LOST_S, f"{len(missing)} events lost: {lost_s:.2f} s before the crash"
    finally:
        stop(second)
        log.close()
