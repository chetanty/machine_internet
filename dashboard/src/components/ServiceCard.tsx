"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Play, Square, Trash2, ExternalLink, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { ServiceSummary } from "@/types";

interface Props {
  service: ServiceSummary;
  onDelete: (id: string) => void;
  onUpdate: (id: string, patch: Partial<ServiceSummary>) => void;
}

export default function ServiceCard({ service, onDelete, onUpdate }: Props) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  async function toggleServe() {
    setBusy(true);
    try {
      if (service.serving) {
        await api.stopServing(service.id);
        onUpdate(service.id, { serving: false, serve_port: null });
      } else {
        const res = await api.startServing(service.id) as { port: number; mcp_url: string };
        onUpdate(service.id, { serving: true, serve_port: res.port });
      }
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!confirm(`Delete ${service.service_name}?`)) return;
    await api.deleteSchema(service.id);
    onDelete(service.id);
  }

  const authBadge: Record<string, string> = {
    none: "bg-gray-100 text-gray-500",
    api_key: "bg-yellow-100 text-yellow-700",
    bearer: "bg-blue-100 text-blue-700",
    oauth2: "bg-purple-100 text-purple-700",
    basic: "bg-orange-100 text-orange-700",
  };

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 flex flex-col gap-4 hover:shadow-sm transition-shadow">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3
            className="font-semibold text-gray-900 text-sm truncate cursor-pointer hover:text-brand-600"
            onClick={() => router.push(`/services/${service.id}`)}
          >
            {service.service_name}
          </h3>
          <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">
            {service.service_description || service.source_url}
          </p>
        </div>
        <span
          className={`shrink-0 text-xs px-2 py-0.5 rounded-full font-medium ${
            authBadge[service.auth_type] ?? authBadge.none
          }`}
        >
          {service.auth_type}
        </span>
      </div>

      <div className="flex items-center gap-2 text-xs text-gray-500">
        <span className="bg-gray-100 rounded-md px-2 py-0.5 font-medium text-gray-700">
          {service.tool_count} tools
        </span>
        {service.serving && service.serve_port && (
          <span className="flex items-center gap-1 text-green-600">
            <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
            :{service.serve_port}
          </span>
        )}
      </div>

      <div className="flex gap-2 mt-auto">
        <button
          onClick={() => router.push(`/services/${service.id}`)}
          className="flex-1 flex items-center justify-center gap-1.5 text-xs border border-gray-300 rounded-lg py-1.5 hover:bg-gray-50 transition-colors"
        >
          <ExternalLink size={12} />
          Open
        </button>
        <button
          onClick={toggleServe}
          disabled={busy}
          className={`flex-1 flex items-center justify-center gap-1.5 text-xs rounded-lg py-1.5 transition-colors ${
            service.serving
              ? "bg-red-50 text-red-600 hover:bg-red-100 border border-red-200"
              : "bg-brand-500 text-white hover:bg-brand-600"
          }`}
        >
          {busy ? (
            <Loader2 size={12} className="animate-spin" />
          ) : service.serving ? (
            <><Square size={12} /> Stop</>
          ) : (
            <><Play size={12} /> Serve</>
          )}
        </button>
        <button
          onClick={remove}
          className="p-1.5 border border-gray-200 rounded-lg text-gray-400 hover:text-red-500 hover:border-red-200 transition-colors"
        >
          <Trash2 size={14} />
        </button>
      </div>
    </div>
  );
}
