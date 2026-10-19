^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Changelog for package jarvis_ros
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Forthcoming
-----------
* First release: the event hub, fault manager, rule engine and recovery orchestrator as lifecycle components.
* The event stream in batches (``/jarvis/event_batches``, ``/jarvis/ingest_batches``); ``/jarvis/events`` while someone reads it.
* Built-in actuators: lifecycle transitions and restarts, std_srvs services, parameters, black-box snapshots.
* Generic actuators (``jarvis_ros::GenericActuator``): ``service.call`` and ``action.goal`` drive any service or action of a type named in the plan, checked against the type when the plans load; ``jarvis_ros::MessageType`` builds and reads messages of types known at run time.
* ``jarvis_load``: load generator for the running stack.
* ``container.restart`` (Docker Engine API) and ``systemd.restart``: drivers that aren't lifecycle nodes, only the containers and units the recovery node allows (``containers.allowed``, ``systemd.allowed_units``).
