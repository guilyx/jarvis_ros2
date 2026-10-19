^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Changelog for package jarvis_mqtt
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Forthcoming
-----------
* First release: devices that don't run ROS report faults, states and logs over MQTT (``<prefix>/devices/<device>/report``); their liveness from their last will (``mqtt.device.offline``); Jarvis's open faults published back, retained (``<prefix>/robots/<robot>/faults``).
