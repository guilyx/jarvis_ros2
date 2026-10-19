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
* The fleet's history: ``fleet.history`` (where the robots upload theirs), ``/api/v1/fleet/history`` per robot, the codes that cost the fleet most, a trend; read once a minute per window.
* ``/api/v1/system`` has ``config``: where the robot's rules and plans come from, with ``config_from``; the dashboard's System page shows it.
* ``/api/v1/health`` has ``config`` too, and ``/api/v1/fleet`` each robot's (the revision it runs, the one it refused and why): the Fleet page shows the rollout.
* Maintenance windows: ``GET``/``POST /api/v1/maintenance``, ``DELETE /api/v1/maintenance/{component}``; notifications about a component under maintenance are silenced (recorded, not sent).
* Rule suggestions: ``GET /api/v1/rules:suggest``, what operators keep doing by hand, drafted as rules.
* Rule lab: ``POST /api/v1/rules:backtest`` replays the robot's history through a draft rule with its other rules and plans (``jarvis backtest``); ``--rules`` and ``--plans``.
