^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Changelog for package jarvis_bridges
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Forthcoming
-----------
* First release: diagnostics, rosout, action status and lifecycle bridges; topic and node watchdogs.
* QoS watchdog: ``ros.topic.qos_incompatible`` while a subscription asks for QoS its publisher cannot match.
* Lifecycle faults name the transition (``left active: inactive after 'deactivate'``): ROS publishes it
  empty, the intermediate state names it.
