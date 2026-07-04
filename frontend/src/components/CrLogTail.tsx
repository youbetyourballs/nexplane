// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useEffect, useRef, useState } from "react";
import { CheckCircle2, XCircle } from "lucide-react";

interface LogLine {
  event: "log";
  step: number | null;
  action_id: string | null;
  ts: string;
  message: string;
}

interface DoneEvent {
  event: "done";
  status: string;
}

interface ErrorEvent {
  event: "error";
  message: string;
}

type WsEvent = LogLine | DoneEvent | ErrorEvent;

interface CrLogTailProps {
  crId: string;
  onDone?: (status: string) => void;
}

function getWsBase(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = window.location.host;
  // In dev, Vite proxies /api → backend; WS path must match
  return `${proto}//${host}`;
}

export function CrLogTail({ crId, onDone }: CrLogTailProps) {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [doneStatus, setDoneStatus] = useState<string | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const token = localStorage.getItem("nexplane_token") ?? "";
    if (!token) {
      setConnectionError("No auth token found");
      return;
    }

    const url = `${getWsBase()}/change-requests/${crId}/log-tail?token=${encodeURIComponent(token)}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onmessage = (evt) => {
      try {
        const data: WsEvent = JSON.parse(evt.data as string);
        if (data.event === "log") {
          setLines((prev) => [...prev, data as LogLine]);
        } else if (data.event === "done") {
          const d = data as DoneEvent;
          setDoneStatus(d.status);
          onDone?.(d.status);
          ws.close();
        } else if (data.event === "error") {
          setConnectionError((data as ErrorEvent).message);
        }
      } catch {
        // ignore parse errors
      }
    };

    ws.onerror = () => {
      setConnectionError("WebSocket connection failed");
    };

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [crId]);

  // Auto-scroll to bottom on new lines
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines]);

  const isDone = doneStatus !== null;
  const isSuccess = doneStatus === "completed";

  return (
    <div className="rounded-lg overflow-hidden border border-slate-700">
      {/* Terminal header */}
      <div className="flex items-center gap-2 px-4 py-2 bg-slate-900 border-b border-slate-700">
        <div className="flex gap-1.5">
          <div className="w-3 h-3 rounded-full bg-red-500 opacity-70" />
          <div className="w-3 h-3 rounded-full bg-yellow-500 opacity-70" />
          <div className="w-3 h-3 rounded-full bg-green-500 opacity-70" />
        </div>
        <span className="text-xs font-mono text-slate-400 ml-2">Execution Log</span>
        {!isDone && (
          <span className="ml-auto flex items-center gap-1.5 text-xs text-emerald-400">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
            </span>
            Live
          </span>
        )}
      </div>

      {/* Log body */}
      <div className="bg-slate-950 font-mono text-xs text-slate-200 px-4 py-3 h-64 overflow-y-auto">
        {connectionError && (
          <div className="text-red-400 mb-2">[error] {connectionError}</div>
        )}
        {lines.length === 0 && !isDone && !connectionError && (
          <div className="text-slate-500">Waiting for log output…</div>
        )}
        {lines.map((line, i) => (
          <div key={i} className="py-0.5 leading-relaxed">
            <span className="text-slate-500 mr-2 select-none">
              {line.ts ? new Date(line.ts).toISOString().substring(11, 19) : ""}
            </span>
            {line.step !== null && (
              <span className="text-brand-400 mr-2">[{line.action_id ?? `step-${line.step}`}]</span>
            )}
            <span>{line.message}</span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Done banner */}
      {isDone && (
        <div
          className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium ${
            isSuccess
              ? "bg-emerald-900/50 text-emerald-300 border-t border-emerald-800"
              : "bg-red-900/50 text-red-300 border-t border-red-800"
          }`}
        >
          {isSuccess ? (
            <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
          ) : (
            <XCircle className="w-4 h-4 flex-shrink-0" />
          )}
          {isSuccess ? "Completed successfully" : `Ended with status: ${doneStatus}`}
        </div>
      )}
    </div>
  );
}
