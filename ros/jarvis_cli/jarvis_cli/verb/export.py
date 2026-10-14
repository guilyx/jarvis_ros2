# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
import json
import pathlib
import sys

from jarvis_interfaces.msg import Event
from rclpy.serialization import deserialize_message
from ros2cli.verb import VerbExtension

from jarvis_cli import api
from jarvis_cli.convert import event_to_dict


def read_events(bag, topic="/jarvis/events"):
    """Yields the Event messages of `topic` in a recorded bag (any storage rosbag2 reads)."""
    import rosbag2_py

    path = pathlib.Path(bag)
    if not path.exists():
        raise api.JarvisError(f"{bag} does not exist")
    reader = rosbag2_py.SequentialReader()
    reader.open(rosbag2_py.StorageOptions(uri=str(path)), rosbag2_py.ConverterOptions("", ""))
    types = {t.name: t.type for t in reader.get_all_topics_and_types()}
    if topic not in types:
        raise api.JarvisError(f"{bag} has no {topic} (topics: {', '.join(sorted(types)) or 'none'})")
    reader.set_filter(rosbag2_py.StorageFilter(topics=[topic]))
    while reader.has_next():
        _, data, _ = reader.read_next()
        yield deserialize_message(data, Event)


def export(bag, out, topic="/jarvis/events"):
    count = 0
    for e in read_events(bag, topic):
        out.write(json.dumps(event_to_dict(e), separators=(",", ":")) + "\n")
        count += 1
    return count


class ExportVerb(VerbExtension):
    """Write the events of a recorded bag as JSON Lines (schemas/event.schema.json)."""

    def add_arguments(self, parser, cli_name):
        parser.add_argument("bag", help="bag directory, e.g. ~/.local/share/jarvis/bags/jarvis_<stamp>")
        parser.add_argument("-o", "--output", default="-", help="file to write (default: stdout)")
        parser.add_argument("--topic", default="/jarvis/events", help="the event topic in the bag")

    @api.run
    def main(self, *, args):
        if args.output == "-":
            export(args.bag, sys.stdout, args.topic)
            return 0
        with open(args.output, "w") as out:
            count = export(args.bag, out, args.topic)
        print(f"{count} events -> {args.output}", file=sys.stderr)
        return 0
