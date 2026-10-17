# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Jarvis on one robot: event hub, fault manager, bridges, recorder and black box in one container.

    ros2 launch jarvis_bringup jarvis.launch.py robot_id:=tb4 use_sim_time:=true

Data lands in `data_dir` (default $JARVIS_DATA, else ~/.local/share/jarvis):
  bags/jarvis_<stamp>/       continuous MCAP of /jarvis/** (+ record_extra_topics), split every 5 min;
                             closed splits are announced on /jarvis/recorder/events/write_split
  blackbox/blackbox_<stamp>/ snapshot-mode ring buffer of blackbox_topics, dumped when a fault asks
  lake/                      closed splits as Parquet (jarvis_lake), for history and analytics:
                             `ros2 run jarvis_lake jarvis-lake sql "SELECT * FROM incidents"`
  state/                     open faults, rule fire history, waiting executions, hub boot counter

The gateway (jarvis_gateway) serves the API, live stream and dashboard on gateway_host:gateway_port
(default 127.0.0.1:8080, this machine only): http://localhost:8080 (the API at /docs). The agent
(jarvis_agent) analyzes incidents and failed recoveries through it and publishes insights.

Rules and plans are directories of YAML files (`rules`, `plans`); the defaults ship generic ones.
"""

import os
import time
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction, SetEnvironmentVariable
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import ComposableNodeContainer, Node
from launch_ros.descriptions import ComposableNode

SHARE = Path(get_package_share_directory("jarvis_bringup"))

ARGUMENTS = {
    "robot_id": ("robot", "Robot id stamped on every event"),
    "data_dir": (os.environ.get("JARVIS_DATA", "~/.local/share/jarvis"), "Root of bags/, blackbox/, state/"),
    "catalog": (str(SHARE / "config" / "faults.yaml"), "Fault catalog (jarvis.faults/v1)"),
    "rules": (str(SHARE / "config" / "rules"), "Directory of rules (jarvis.rules/v1)"),
    "plans": (str(SHARE / "config" / "plans"), "Directory of recovery plans (jarvis.plans/v1)"),
    "recovery": ("true", "Run the rule engine and the recovery orchestrator"),
    "recovery_mode": ("ENABLED", "Initial recovery mode: ENABLED, DRY_RUN or DISABLED"),
    "params_file": (str(SHARE / "config" / "jarvis.yaml"), "Parameters of the Jarvis components"),
    "use_sim_time": ("false", "Use /clock"),
    "autostart": ("true", "Configure and activate the lifecycle components on startup"),
    "bridges": ("true", "Bridge /diagnostics, /rosout, action goal status, lifecycle and watchdogs"),
    "record": ("true", "Record /jarvis/** to MCAP"),
    "record_extra_topics": ("/diagnostics /rosout", "Context topics recorded with /jarvis/**"),
    "split_duration_s": ("300", "Length of a recorder split"),
    "recorder_storage_config": (
        str(SHARE / "config" / "mcap_writer.yaml"),
        "MCAP writer settings of the recorder: what a crash loses vs. compression",
    ),
    "blackbox": ("true", "Run the snapshot-mode black-box recorder"),
    "blackbox_topics": ("/tf /tf_static /odom /scan /cmd_vel /jarvis/events", "Black-box topics"),
    "blackbox_cache_mb": ("256", "Memory of the black-box ring buffer"),
    "lake": ("true", "Convert closed splits to Parquet and enforce retention (jarvis_lake)"),
    "lake_min_free_gib": ("1", "Free disk the lake keeps by deleting the oldest data (disk pressure)"),
    "gateway": ("true", "Serve the API, live stream and dashboard (jarvis_gateway)"),
    "gateway_host": ("127.0.0.1", "Address the gateway binds (a LAN address to reach it from others)"),
    "gateway_port": ("8080", "Port of the gateway"),
    "gateway_config": ("", "Gateway settings (jarvis.gateway/v1): tokens, webhooks, ..."),
    "gateway_dashboard": ("", "Built dashboard to serve (default: the installed one, if any)"),
    "agent": ("true", "Analyze incidents and failed recoveries (jarvis_agent, through the gateway)"),
    "agent_config": ("", "Agent settings (jarvis.agent/v1): autonomy, triggers, redaction"),
    "log_level": ("info", "Log level of the container"),
    # One thread per component: an event costs half the CPU of the multi-threaded container's
    # shared executor (docs/design/01-requirements.md §5, measured), and a slow one can't stall
    # the others.
    "container": ("component_container_isolated", "rclcpp_components container executable"),
    # Fast DDS caps histories at 5 000 samples whatever their depth: the profile lifts it for the
    # event stream's 20 000-deep queues. Not applied when a profile file is already set.
    "fastdds_profile": (str(SHARE / "config" / "fastdds.xml"), "Fast DDS profile of the Jarvis processes"),
}


def _flag(value):
    return value.strip().lower() in ("1", "true", "yes", "on")


def _setup(context):
    arg = {name: LaunchConfiguration(name).perform(context) for name in ARGUMENTS}
    data_dir = Path(arg["data_dir"]).expanduser()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    use_sim_time = _flag(arg["use_sim_time"])
    autostart = _flag(arg["autostart"])

    common = {"robot_id": arg["robot_id"], "use_sim_time": use_sim_time, "autostart": autostart}
    state = {"state_dir": str(data_dir / "state")}
    fast = [{"use_intra_process_comms": True}]

    nodes = [
        ComposableNode(
            package="jarvis_ros",
            plugin="jarvis_ros::EventHubNode",
            name="event_hub",
            namespace="jarvis",
            parameters=[arg["params_file"], {**common, **state}],
            extra_arguments=fast,
        ),
        ComposableNode(
            package="jarvis_ros",
            plugin="jarvis_ros::FaultManagerNode",
            name="fault_manager",
            namespace="jarvis",
            parameters=[arg["params_file"], {**common, **state, "catalog_path": arg["catalog"]}],
            extra_arguments=fast,
        ),
    ]

    if _flag(arg["recovery"]):
        nodes += [
            ComposableNode(
                package="jarvis_ros",
                plugin="jarvis_ros::RuleEngineNode",
                name="rule_engine",
                namespace="jarvis",
                parameters=[arg["params_file"], {**common, **state, "rules_dir": arg["rules"]}],
                extra_arguments=fast,
            ),
            ComposableNode(
                package="jarvis_ros",
                plugin="jarvis_ros::RecoveryNode",
                name="recovery",
                namespace="jarvis",
                parameters=[
                    arg["params_file"],
                    {**common, **state, "plans_dir": arg["plans"], "mode": arg["recovery_mode"]},
                ],
                extra_arguments=fast,
            ),
        ]

    if _flag(arg["bridges"]):
        for plugin, name in (
            ("DiagnosticsBridge", "diagnostics"),
            ("RosoutBridge", "rosout"),
            ("ActionStatusBridge", "action_status"),
            ("LifecycleBridge", "lifecycle"),
            ("TopicWatchdogBridge", "topic_watchdog"),
            ("NodeWatchdogBridge", "node_watchdog"),
        ):
            nodes.append(
                ComposableNode(
                    package="jarvis_bridges",
                    plugin=f"jarvis_bridges::{plugin}",
                    name=name,
                    namespace="jarvis/bridges",
                    parameters=[arg["params_file"], {"use_sim_time": use_sim_time, "autostart": autostart}],
                )
            )

    if _flag(arg["record"]):
        (data_dir / "bags").mkdir(parents=True, exist_ok=True)
        nodes.append(
            ComposableNode(
                package="rosbag2_transport",
                plugin="rosbag2_transport::Recorder",
                name="recorder",
                namespace="jarvis",
                # rosbag2 announces closed splits on a relative topic: keep the two recorders apart.
                remappings=[("events/write_split", "/jarvis/recorder/events/write_split")],
                parameters=[
                    {
                        "use_sim_time": use_sim_time,
                        "storage.uri": str(data_dir / "bags" / f"jarvis_{stamp}"),
                        "storage.storage_id": "mcap",
                        # What a crash loses (NFR-6): config/mcap_writer.yaml.
                        "storage.storage_config_uri": arg["recorder_storage_config"],
                        "storage.max_bagfile_duration": int(arg["split_duration_s"]),
                        "record.regex": "^/jarvis/.*",
                        # The ingest side is pre-validation; /jarvis/events is the canonical stream.
                        "record.exclude_regex": "^/jarvis/(ingest|recorder/.*|blackbox/.*)$",
                        "record.topics": arg["record_extra_topics"].split(),
                        "record.qos_profile_overrides_path": str(SHARE / "config" / "recorder_qos.yaml"),
                        "record.disable_keyboard_controls": True,
                    }
                ],
            )
        )

    if _flag(arg["blackbox"]):
        (data_dir / "blackbox").mkdir(parents=True, exist_ok=True)
        nodes.append(
            ComposableNode(
                package="rosbag2_transport",
                plugin="rosbag2_transport::Recorder",
                name="blackbox",
                namespace="jarvis",
                remappings=[("events/write_split", "/jarvis/blackbox/events/write_split")],
                parameters=[
                    {
                        "use_sim_time": use_sim_time,
                        "storage.uri": str(data_dir / "blackbox" / f"blackbox_{stamp}"),
                        "storage.storage_id": "mcap",
                        "storage.snapshot_mode": True,
                        "storage.max_cache_size": int(arg["blackbox_cache_mb"]) << 20,
                        "record.topics": arg["blackbox_topics"].split(),
                        "record.disable_keyboard_controls": True,
                    }
                ],
            )
        )

    processes = []
    if _flag(arg["lake"]):
        # Its own process: disk-heavy, third-party libraries, and nothing else waits on it.
        processes.append(
            Node(
                package="jarvis_lake",
                executable="jarvis-lake",
                name="lake",
                namespace="jarvis",
                arguments=["--data-dir", str(data_dir), "--min-free-gib", arg["lake_min_free_gib"], "run"],
                output="screen",
            )
        )
    if _flag(arg["gateway"]):
        config = ["--config", arg["gateway_config"]] if arg["gateway_config"] else []
        if arg["gateway_dashboard"]:
            config += ["--dashboard", arg["gateway_dashboard"]]
        processes.append(
            Node(
                package="jarvis_gateway",
                executable="jarvis-gateway",
                name="gateway",
                namespace="jarvis",
                arguments=[
                    *config,
                    "--data-dir",
                    str(data_dir),
                    "--host",
                    arg["gateway_host"],
                    "--port",
                    arg["gateway_port"],
                ],
                output="screen",
            )
        )
    if _flag(arg["gateway"]) and _flag(arg["agent"]):
        # It reads through the gateway (history and the live stream) and publishes INSIGHT events.
        config = ["--config", arg["agent_config"]] if arg["agent_config"] else []
        processes.append(
            Node(
                package="jarvis_agent",
                executable="jarvis-agent",
                name="agent",
                namespace="jarvis",
                arguments=[*config, "--gateway", f"http://127.0.0.1:{arg['gateway_port']}", "run"],
                output="screen",
            )
        )

    env = []
    if arg["fastdds_profile"] and not (
        os.environ.get("FASTRTPS_DEFAULT_PROFILES_FILE") or os.environ.get("FASTDDS_DEFAULT_PROFILES_FILE")
    ):
        env.append(SetEnvironmentVariable("FASTRTPS_DEFAULT_PROFILES_FILE", arg["fastdds_profile"]))
    return [
        *env,
        *processes,
        ComposableNodeContainer(
            name="container",
            namespace="jarvis",
            package="rclcpp_components",
            executable=arg["container"],
            composable_node_descriptions=nodes,
            arguments=["--ros-args", "--log-level", arg["log_level"]],
            output="screen",
        ),
    ]


def generate_launch_description():
    arguments = [
        DeclareLaunchArgument(name, default_value=default, description=doc)
        for name, (default, doc) in ARGUMENTS.items()
    ]
    return LaunchDescription([*arguments, OpaqueFunction(function=_setup)])
