// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Brain, Search, X } from "lucide-react";
import { infrastructureMemoryApi } from "../api/endpoints";
import { useDebounce } from "../hooks/useDebounce";

const ENV_COLORS: Record<string, string> = {
  prod: "bg-red-500/10 text-red-400 border-red-400/30",
  staging: "bg-amber-500/10 text-amber-400 border-amber-400/30",
  dev: "bg-slate-500/10 text-slate-400 border-slate-400/30",
};

export function InfrastructureMemoryPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [inputValue, setInputValue] = useState(searchParams.get("q") ?? "");
  const debouncedQ = useDebounce(inputValue, 300);
  const page = parseInt(searchParams.get("page") ?? "1", 10);
  const pageSize = 50;

  const { data, isLoading } = useQuery({
    queryKey: ["infrastructure-memory", debouncedQ, page],
    queryFn: () =>
      infrastructureMemoryApi.list({ q: debouncedQ || undefined, page, page_size: pageSize }),
  });

  function handleSearchChange(value: string) {
    setInputValue(value);
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set("q", value);
      else next.delete("q");
      next.delete("page");
      return next;
    });
  }

  function handleClearSearch() {
    setInputValue("");
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("q");
      next.delete("page");
      return next;
    });
  }

  function handlePageChange(newPage: number) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("page", String(newPage));
      return next;
    });
  }

  const totalPages = data ? Math.ceil(data.total / pageSize) : 1;

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center gap-3 mb-2">
        <div className="p-2 bg-amber-500/10 rounded-lg">
          <Brain className="w-5 h-5 text-amber-400" />
        </div>
        <h1 className="text-2xl font-bold text-white">Infrastructure Memory</h1>
      </div>
      <p className="text-slate-400 mb-6">Why does this exist?</p>

      <div className="relative mb-6 max-w-lg">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
        <input
          type="text"
          placeholder="Search by name, owner, or reason…"
          value={inputValue}
          onChange={(e) => handleSearchChange(e.target.value)}
          className="w-full pl-9 pr-9 py-2 bg-navy-light border border-navy-border rounded-lg text-sm text-white placeholder-slate-500 focus:outline-none focus:border-brand-400"
        />
        {inputValue && (
          <button
            onClick={handleClearSearch}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-white"
          >
            <X className="w-4 h-4" />
          </button>
        )}
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="h-12 bg-navy-light border border-navy-border rounded animate-pulse" />
          ))}
        </div>
      ) : !data || data.items.length === 0 ? (
        <div className="text-center py-16 text-slate-500">
          <p className="text-base mb-2">
            {inputValue ? "No assets match your search." : "No assets found."}
          </p>
          {inputValue && (
            <button onClick={handleClearSearch} className="text-sm text-brand-400 hover:text-brand-300">
              Clear search
            </button>
          )}
        </div>
      ) : (
        <>
          <p className="text-xs text-slate-500 mb-3">
            {data.total} asset{data.total !== 1 ? "s" : ""}
          </p>
          <div className="border border-navy-border rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-navy-border bg-navy-light">
                  <th className="text-left px-4 py-3 text-slate-400 font-medium">Asset</th>
                  <th className="text-left px-4 py-3 text-slate-400 font-medium">Owner</th>
                  <th className="text-left px-4 py-3 text-slate-400 font-medium">Why it exists</th>
                  <th className="text-left px-4 py-3 text-slate-400 font-medium">Type</th>
                  <th className="text-left px-4 py-3 text-slate-400 font-medium">Env</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr
                    key={item.id}
                    className="border-b border-navy-border last:border-0 hover:bg-navy-light/50 transition-colors"
                  >
                    <td className="px-4 py-3">
                      <Link to={`/assets/${item.id}`} className="text-brand-400 hover:text-brand-300 font-medium">
                        {item.name}
                      </Link>
                    </td>
                    <td className="px-4 py-3">
                      {item.owner ? (
                        <span className="text-xs px-2 py-0.5 rounded border bg-slate-500/10 text-slate-300 border-slate-400/30">
                          {item.owner}
                        </span>
                      ) : (
                        <span className="text-slate-600">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-slate-300 max-w-sm">
                      {item.why_exists ? (
                        <span title={item.why_exists}>
                          {item.why_exists.length > 120
                            ? item.why_exists.slice(0, 117) + "…"
                            : item.why_exists}
                        </span>
                      ) : (
                        <span className="text-slate-600">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className="text-xs text-slate-400">{item.asset_type.replace(/_/g, " ")}</span>
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`text-xs px-2 py-0.5 rounded border ${
                          ENV_COLORS[item.environment] ?? "text-slate-400"
                        }`}
                      >
                        {item.environment}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {totalPages > 1 && (
            <div className="flex items-center gap-3 mt-4 justify-end">
              <button
                onClick={() => handlePageChange(page - 1)}
                disabled={page <= 1}
                className="text-sm px-3 py-1.5 rounded border border-navy-border text-slate-400 hover:text-white disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Previous
              </button>
              <span className="text-sm text-slate-500">
                Page {page} of {totalPages}
              </span>
              <button
                onClick={() => handlePageChange(page + 1)}
                disabled={page >= totalPages}
                className="text-sm px-3 py-1.5 rounded border border-navy-border text-slate-400 hover:text-white disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Next
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
