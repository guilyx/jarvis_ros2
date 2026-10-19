// Copyright 2026 Erwin L.
// SPDX-License-Identifier: Apache-2.0
import { ExtensionContext } from "@foxglove/extension";

import { initJarvisPanel } from "./JarvisPanel";

export function activate(extensionContext: ExtensionContext): void {
  extensionContext.registerPanel({ name: "Jarvis", initPanel: initJarvisPanel });
}
