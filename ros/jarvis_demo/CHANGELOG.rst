^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Changelog for package jarvis_demo
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Forthcoming
-----------
* First release: the lidar relay, the fault injector and its narrated tour, the live monitor, and the demo and Nav2 simulation launch files.
* The docking demo (``docking.launch.py``): a robot base with a battery, Nav2's docking action and AMCL's initial pose service, docked by a plan of generic steps.
* The container scenario (``containers.launch.py``): the lidar driver in a container, restarted by Jarvis when it hangs (``fake_lidar`` ``inject: hang``).
* The behavior-tree scenario (``trees.launch.py``): a behavior tree restarts the wedged lidar relay, gently first.
* The fault injector asks the relay again when its parameter reply is lost to discovery (a CI flake).
