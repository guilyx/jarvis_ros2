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
    name="jarvis_gateway",
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/jarvis_gateway"]),
        ("share/jarvis_gateway", ["package.xml", "requirements.txt"]),
        ("share/jarvis_gateway/config", ["config/gateway.yaml"]),
    ],
    install_requires=REQUIREMENTS,
    zip_safe=True,
    maintainer="Erwin L.",
    maintainer_email="erwin.lejeune15@gmail.com",
    description="Jarvis gateway: REST API, live WebSocket stream, webhooks and metrics for the dashboard",
    license="Apache-2.0",
    extras_require={"test": ["pytest"]},
    entry_points={"console_scripts": ["jarvis-gateway = jarvis_gateway.cli:main"]},
)
