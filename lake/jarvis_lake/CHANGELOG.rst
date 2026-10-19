^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Changelog for package jarvis_lake
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Forthcoming
-----------
* First release: recordings converted to Parquet (crash recovery included), DuckDB views of events, faults, incidents, recoveries and rules, KPIs and analytics, retention with a free-disk floor.
* History off the robot: ``--upload-to`` (S3, any S3 API, a mounted share) uploads the Parquet, and with ``--upload-blackbox`` the black box's snapshots, as they are complete; retention waits for them unless the disk runs short; ``jarvis.upload.behind`` past ``--upload-max-lag-hours``; ``jarvis-lake upload`` and ``jarvis-lake fleet`` (the lake's views over every robot's uploads, with a ``robot`` column, and ``--summary`` as the dashboard shows it); ``jarvis-lake demo-history``, a made-up week for demo robots.
