# Security policy

Jarvis runs on robots: a weakness in it can move hardware. Please report vulnerabilities
privately, not in a public issue.

## Reporting

Use GitHub's private vulnerability reporting: **Security → Report a vulnerability** on
<https://github.com/guilyx/jarvis_ros2>. Say what is affected (package, version or commit), how to
reproduce it, and what an attacker gains. You will get an answer within 7 days, and a fix or a
plan within 30 for anything confirmed. Reporters are credited in the release notes unless they
prefer not to be.

## Supported versions

The latest release gets security fixes. Before 1.0, only `main`.

## Scope

In scope: everything in this repository: the core and its ROS components, the gateway (API,
live stream, authentication, roles), the dashboard, the agent (what reaches a model), the lake,
the Docker images, and the SROS2 profile.

Hardening a deployment is described in [docs/guides/security.md](docs/guides/security.md). A
deployment that leaves the gateway open on a network without TLS or sign-in, or runs the ROS
graph without SROS2 where untrusted machines can join it, is a configuration issue, not a
vulnerability in Jarvis; unsafe defaults are, and are welcome reports.
