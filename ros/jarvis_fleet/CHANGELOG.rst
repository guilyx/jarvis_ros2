^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Changelog for package jarvis_fleet
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Forthcoming
-----------
* First release: ``config_sync`` takes the robot's rules and plans from the fleet's storage (``config_from``: S3, MinIO, a mounted share), shared and per robot; a new revision settles, passes ``jarvis lint`` and the recovery orchestrator's and rule engine's own checks, then loads (plans first) with an atomic switch of ``<data_dir>/config/active``; any failure keeps the revision before. A refused one raises ``jarvis.config.rejected``.
