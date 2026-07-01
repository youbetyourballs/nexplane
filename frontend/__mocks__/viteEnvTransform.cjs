// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

// Custom Jest transform: strips Vite-specific import.meta.env references
// before ts-jest processes the file, so Node.js (CommonJS mode) doesn't choke.
const { TsJestTransformer } = require("ts-jest");

const transformer = new TsJestTransformer({
  tsconfig: {
    jsx: "react-jsx",
    esModuleInterop: true,
    allowSyntheticDefaultImports: true,
    moduleResolution: "node",
    module: "CommonJS",
  },
  useESM: false,
});

module.exports = {
  process(sourceText, sourcePath, options) {
    // Replace import.meta.env.X with undefined-safe access; all call sites
    // use `|| "fallback"` so undefined is fine in tests.
    const patched = sourceText.replace(
      /import\.meta\.env(?:\.(\w+))?/g,
      (_, key) => (key ? `(typeof __VITE_ENV__ !== "undefined" ? __VITE_ENV__["${key}"] : undefined)` : "({})")
    );
    return transformer.process(patched, sourcePath, options);
  },
};
