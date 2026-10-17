# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The whole stack under load (milestone 5.1): jarvis_load at a modest rate and a burst, nothing
lost, the latency bounded. CI machines are slower and shared than a dev machine: the NFR rates are
checked by the nightly soak (.github/workflows/soak.yml), not here."""

import json
import os
import pathlib
import subprocess
import tempfile
import unittest

import launch
import launch_testing
import launch_testing.actions
import launch_testing.asserts
import pytest
from ament_index_python.packages import get_package_prefix, get_package_share_directory
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource

DATA_DIR = tempfile.mkdtemp(prefix="jarvis_load_")
BRINGUP = pathlib.Path(get_package_share_directory("jarvis_bringup")) / "launch" / "jarvis.launch.py"
LOAD = pathlib.Path(get_package_prefix("jarvis_ros")) / "lib" / "jarvis_ros" / "jarvis_load"
# The load tool sends its burst from one publisher: it needs the history cap Jarvis lifts too.
PROFILE = BRINGUP.parent.parent / "config" / "fastdds.xml"


@pytest.mark.launch_test
def generate_test_description():
    jarvis = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(str(BRINGUP)),
        launch_arguments={
            "robot_id": "load-bot",
            "data_dir": DATA_DIR,
            "gateway": "false",
            "agent": "false",
        }.items(),
    )
    return launch.LaunchDescription([jarvis, launch_testing.actions.ReadyToTest()])


class TestLoad(unittest.TestCase):
    def test_a_fleet_and_a_burst_get_through(self):
        out = pathlib.Path(DATA_DIR) / "load.json"
        args = ["--rate", "500", "--seconds", "10", "--burst", "5000", "--sources", "200"]
        run = subprocess.run(
            [str(LOAD), *args, "--check", "--max-p99-ms", "250", "--json", str(out)],
            capture_output=True,
            text=True,
            timeout=150,
            env={"FASTRTPS_DEFAULT_PROFILES_FILE": str(PROFILE), **os.environ},
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        r = json.loads(out.read_text())
        self.assertEqual(r["sent"], 10000)
        self.assertEqual(r["lost"], 0, r)
        self.assertTrue(r["process"]["pids"], "the container's RSS and CPU were measured")


@launch_testing.post_shutdown_test()
class TestShutdown(unittest.TestCase):
    def test_exit_code(self, proc_info):
        launch_testing.asserts.assertExitCodes(proc_info, allowable_exit_codes=[0, -2, -15])
