# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
from setuptools import find_packages, setup

setup(
    name="jarvis_mcp",
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/jarvis_mcp"]),
        ("share/jarvis_mcp", ["package.xml"]),
    ],
    install_requires=[],
    zip_safe=True,
    maintainer="Erwin L.",
    maintainer_email="erwin.lejeune15@gmail.com",
    description="Jarvis for AI assistants: an MCP server over the Jarvis gateway",
    license="Apache-2.0",
    extras_require={"test": ["pytest"]},
    entry_points={"console_scripts": ["jarvis-mcp = jarvis_mcp.cli:main"]},
)
