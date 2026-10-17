# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
import pathlib

from setuptools import find_packages, setup

HERE = pathlib.Path(__file__).parent
REQUIREMENTS = [
    line.split("#")[0].strip()
    for line in (HERE / "requirements.txt").read_text().splitlines()
    if line.split("#")[0].strip()
]

setup(
    name="jarvis_agent",
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/jarvis_agent"]),
        ("share/jarvis_agent", ["package.xml", "requirements.txt"]),
        ("share/jarvis_agent/config", ["config/agent.yaml"]),
    ],
    install_requires=REQUIREMENTS,
    zip_safe=True,
    maintainer="Erwin L.",
    maintainer_email="erwin.lejeune15@gmail.com",
    description="Jarvis agent: evidence-backed insights and recovery advice from windows of events",
    license="Apache-2.0",
    extras_require={"test": ["pytest"]},
    entry_points={"console_scripts": ["jarvis-agent = jarvis_agent.cli:main"]},
)
