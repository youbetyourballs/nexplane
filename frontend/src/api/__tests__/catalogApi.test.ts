// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

jest.mock("../client", () => ({ apiClient: { get: jest.fn(), post: jest.fn() } }));
import { apiClient } from "../client";
import { capabilitiesApi, catalogApi } from "../endpoints";

test("capabilitiesApi.get hits /capabilities", async () => {
  (apiClient.get as jest.Mock).mockResolvedValue({ data: { edition: "commercial", commercial: true, domains: ["commercial"] } });
  const c = await capabilitiesApi.get();
  expect((apiClient.get as jest.Mock).mock.calls[0][0]).toBe("/capabilities");
  expect(c.commercial).toBe(true);
});

test("catalogApi.actions passes domain param + run posts", async () => {
  (apiClient.get as jest.Mock).mockResolvedValue({ data: [] });
  await catalogApi.actions("commercial");
  expect((apiClient.get as jest.Mock).mock.calls.at(-1)[0]).toContain("/catalog/actions");
  (apiClient.post as jest.Mock).mockResolvedValue({ data: { customers: [] } });
  await catalogApi.run({ connector_type: "commercial", action_id: "list_customers", params: {} });
  expect((apiClient.post as jest.Mock).mock.calls.at(-1)[0]).toBe("/catalog/run");
});
