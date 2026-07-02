// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.
import { useQuery } from "@tanstack/react-query";
import { capabilitiesApi } from "../api/endpoints";

export function useCapabilities() {
  return useQuery({ queryKey: ["capabilities"], queryFn: capabilitiesApi.get, staleTime: 5 * 60_000 });
}
