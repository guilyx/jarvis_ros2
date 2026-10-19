# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""The systemd unit (systemd/jarvis.service) and its settings (jarvis.env) agree with each other and
with jarvis.launch.py: every variable the unit uses is set, every launch argument they pass exists,
and systemd reads the unit without complaint (where systemd-analyze is installed)."""

import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).parents[1]
UNIT = ROOT / "systemd" / "jarvis.service"
ENV = ROOT / "systemd" / "jarvis.env"
LAUNCH = ROOT / "launch" / "jarvis.launch.py"


def exec_start():
    # systemd joins lines that end with a backslash.
    text = UNIT.read_text().replace("\\\n", " ")
    return next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("ExecStart="))


def settings(commented=False):
    pattern = r"^#?\s*([A-Z_]+)=(.*)$" if commented else r"^([A-Z_]+)=(.*)$"
    return dict(re.findall(pattern, ENV.read_text(), re.MULTILINE))


def launch_arguments():
    return set(re.findall(r'^    "(\w+)": \(', LAUNCH.read_text(), re.MULTILINE))


def test_the_unit_uses_only_what_the_settings_set():
    used = set(re.findall(r"\$\{(\w+)\}", exec_start()))
    assert used == {"ROS_SETUP", "JARVIS_ROBOT_ID", "JARVIS_DATA", "JARVIS_LAUNCH_ARGS"}
    assert used <= set(settings())


def test_the_launch_arguments_exist():
    arguments = launch_arguments()
    assert {"robot_id", "data_dir", "gateway_host"} <= arguments
    passed = set(re.findall(r"(\w+):=", exec_start()))
    passed |= {a for value in settings(commented=True).values() for a in re.findall(r"(\w+):=", value)}
    assert passed and passed <= arguments, passed - arguments


def test_the_data_directory_is_the_units_state_directory():
    assert "StateDirectory=jarvis\n" in UNIT.read_text()
    assert settings()["JARVIS_DATA"] == "/var/lib/jarvis"


def test_it_stops_like_ctrl_c():
    unit = UNIT.read_text()
    assert "KillSignal=SIGINT\n" in unit and "KillMode=mixed\n" in unit


@pytest.mark.skipif(not shutil.which("systemd-analyze"), reason="no systemd-analyze here")
def test_systemd_reads_the_unit(tmp_path):
    unit = UNIT.read_text().replace("EnvironmentFile=/etc/jarvis/jarvis.env", f"EnvironmentFile={ENV}")
    (tmp_path / "jarvis.service").write_text(unit)
    out = subprocess.run(
        ["systemd-analyze", "verify", str(tmp_path / "jarvis.service")], capture_output=True, text=True
    )
    # Its user and its target exist on a robot, not necessarily here: only the unit's own syntax counts.
    problems = [
        line
        for line in (out.stdout + out.stderr).splitlines()
        if line.strip() and "jarvis" in line and "User" not in line and "multi-user" not in line
    ]
    assert not problems, problems
