^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Changelog for package jarvis_bringup
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Forthcoming
-----------
* First release: the launch file (one container, the recorder and the black box, the lake, the gateway, the agent), parameters, the default fault catalog with a runbook per code, rules and plans.
* Crash durability (unchunked MCAP recording) and an SROS2 profile with one enclave per process.
* ``medkit:=true``: ros2_medkit's faults into Jarvis (``jarvis_medkit``), in the ``/jarvis/medkit`` enclave.
* ``mqtt:=true``: devices that don't run ROS report over MQTT (``jarvis_mqtt``), in the ``/jarvis/mqtt`` enclave; ``mqtt.device.offline`` and ``mqtt.broker.unreachable`` in the catalog, with runbooks.
* Plans ``restart-container`` and ``restart-systemd-unit``; ``params_overlay`` layers a parameters file over ``jarvis.yaml``.
