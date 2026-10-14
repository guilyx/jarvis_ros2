# Who emitted an event and what it is about.
string robot      # robot / system id
string node       # emitting node (fully qualified) or any producer id outside ROS
string component  # logical thing the event is about; empty = the node itself
string host
