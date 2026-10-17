# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""Prints the gateway's OpenAPI document: the dashboard generates its typed client from it.

python3 -m jarvis_gateway.openapi > openapi.json      (dashboard: npm run gen:api)
"""

import json
import sys
import tempfile

from jarvis_gateway.adapters.fake import FakeJarvis
from jarvis_gateway.app import create_app
from jarvis_gateway.config import Settings


def main():
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(Settings(data_dir=tmp), FakeJarvis())
        json.dump(app.openapi(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
