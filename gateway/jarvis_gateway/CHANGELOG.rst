^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Changelog for package jarvis_gateway
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Forthcoming
-----------
* First release: REST API and a gap-free live WebSocket stream; attributed commands with roles and an audit trail; webhooks (Slack, JSON); Prometheus metrics; the fault catalog and runbooks; the dashboard.
* TLS, and single sign-on through an authenticating proxy.
* A SOVD-style API at ``/sovd/api/v1`` for SOVD clients (the ros2_medkit web UI, its Foxglove panel); ``cors_origins``.
* VDA 5050: open faults as the ``errors`` of a robot's state, ``/api/v1/vda5050/errors``; ``vda5050`` settings.
* Fleet: other robots' gateways side by side, ``/api/v1/fleet``; ``fleet`` settings.
