# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""ros2 jarvis maintenance: how long, as people write it."""

import pytest
from jarvis_cli.api import JarvisError
from jarvis_cli.verb.maintenance import seconds


def test_durations_as_people_write_them():
    assert seconds("90") == 90
    assert seconds("90s") == 90
    assert seconds("30m") == 30 * 60 == seconds("30min")
    assert seconds("1.5h") == 5400
    assert seconds("1d") == 86400
    for bad in ("", "soon", "30 minutes", "-5m"):
        with pytest.raises(JarvisError):
            seconds(bad)
