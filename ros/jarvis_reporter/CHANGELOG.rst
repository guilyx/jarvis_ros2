^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Changelog for package jarvis_reporter
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Forthcoming
-----------
* First release: report faults, states and logs from C++ (header only) and Python nodes.
* Python: ``publisher=`` shares one publisher among reporters (a bridge reporting for several components): a new publisher isn't matched to the event hub at once, and what it sends first can be lost.
