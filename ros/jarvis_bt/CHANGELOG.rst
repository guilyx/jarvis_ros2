^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Changelog for package jarvis_bt
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Forthcoming
-----------
* First release: ``bt.run`` (``jarvis_bt::TreeActuator``) runs a BehaviorTree.CPP 4 tree from ``bt.trees_dir`` as a recovery step, with the libraries in ``bt.plugins``; the step's message says what each named node did.
* BT nodes: ``JarvisCallService``, ``JarvisSendGoal`` (any service or action, types by name), ``JarvisFaultActive``, ``JarvisWaitFaultCleared`` (Jarvis's open faults); also as the plugin library ``jarvis_bt_nodes``.
* ``trees/restart_lifecycle_node.xml``: reactivate a lifecycle node, then reconfigure it if its fault stays.
