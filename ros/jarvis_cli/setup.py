# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
from setuptools import find_packages, setup

VERBS = {
    "faults": "FaultsVerb",
    "rules": "RulesVerb",
    "plans": "PlansVerb",
    "recover": "RecoverVerb",
    "mode": "ModeVerb",
    "maintenance": "MaintenanceVerb",
    "approve": "ApproveVerb",
    "reject": "RejectVerb",
    "cancel": "CancelVerb",
    "executions": "ExecutionsVerb",
    "export": "ExportVerb",
    "backtest": "BacktestVerb",
    "lint": "LintVerb",
    "doctor": "DoctorVerb",
}

setup(
    name="jarvis_cli",
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/jarvis_cli"]),
        ("share/jarvis_cli", ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Erwin L.",
    maintainer_email="erwin.lejeune15@gmail.com",
    description="ros2 jarvis: operate Jarvis from the command line",
    license="Apache-2.0",
    extras_require={"test": ["pytest"]},
    entry_points={
        "ros2cli.command": ["jarvis = jarvis_cli.command.jarvis:JarvisCommand"],
        "jarvis_cli.verb": [f"{name} = jarvis_cli.verb.{name}:{cls}" for name, cls in VERBS.items()],
    },
)
