// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

// Jest setupFiles shim: defines import.meta.env so Vite source files that
// reference import.meta.env can be compiled under ts-jest (CommonJS mode).
Object.defineProperty(globalThis, "import", {
  value: { meta: { env: {} } },
  writable: true,
  configurable: true,
});
