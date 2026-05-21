"use client";
import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Play, Square, Copy, Check, Loader2 } from "lucide-react";
import ToolTester from "@/components/ToolTester";
import { api } from "@/lib/api";
import type { ServiceDetail } from "@/types";

export default function ServicePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [service, setService] = useState<ServiceDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    api.getSchema(id)
      .then((d) => setService(d as ServiceDetail))
      .finally(() => setLoading(false));
  }, [id]);

  async function toggleServe() {
    if (!service) return;
    setBusy(true);
    try {
      if (service.serving) {
        await api.stopServing(id);
        setService((s) => s ? { ...s, serving: false, serve_port: null } : s);
      } else {
        const res = await api.startServing(id) as { port: number };
        setService((s) => s ? { ...s, serving: true, serve_port: res.port } : s);
      }
    } finally {
      setBusy(false);
    }
  }

  function copyMcpUrl() {
    if (!service?.serve_port) return;
    navigator.clipboard.writeText(`http://localhost:${service.serve_port}/mcp`);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  if (loading) return (
    <div className="flex items-center justify-center py-32 text-gray-400 text-sm">
      Loading…
    </div>
  );

  if (!service) return (
    <div className="text-center py-32 text-gray-500 text-sm">Service not found.</div>
  );

  return (
    <div className="max-w-4xl mx-auto px-6 py-8">
      <button
        onClick={() => router.push("/")}
        className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 mb-6"
      >
        <ArrowLeft size={14} />
        Back
      </button>

      <div className="flex items-start justify-between gap-4 mb-8">
        <div>
          <h1 className="text-xl font-bold text-gray-900">{service.service_name}</h1>
          <p className="text-sm text-gray-500 mt-1">{service.service_description}</p>
          {service.source_url && (
            <a
              href={service.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-brand-600 hover:underline mt-0.5 block"
            >
              {service.source_url}
            </a>
          )}
        </div>

        <div className="flex gap-2 shrink-0">
          {service.serving && service.serve_port && (
            <button
              onClick={copyMcpUrl}
              className="flex items-center gap-1.5 border border-gray-300 rounded-lg px-3 py-2 text-xs hover:bg-gray-50"
            >
              {copied ? <Check size={12} className="text-green-500" /> : <Copy size={12} />}
              {copied ? "Copied!" : `Copy MCP URL`}
            </button>
          )}
          <button
            onClick={toggleServe}
            disabled={busy}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium transition-colors ${
              service.serving
                ? "bg-red-50 text-red-600 border border-red-200 hover:bg-red-100"
                : "bg-brand-500 text-white hover:bg-brand-600"
            }`}
          >
            {busy ? <Loader2 size={12} className="animate-spin" /> :
              service.serving ? <><Square size={12} /> Stop</> : <><Play size={12} /> Serve</>}
          </button>
        </div>
      </div>

      {service.serving && service.serve_port && (
        <div className="bg-green-50 border border-green-200 rounded-lg px-4 py-3 mb-6 flex items-center gap-3">
          <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
          <code className="text-xs text-green-800 font-mono">
            http://localhost:{service.serve_port}/mcp
          </code>
          <span className="text-xs text-green-600 ml-auto">Active MCP endpoint</span>
        </div>
      )}

      <div className="mb-4">
        <h2 className="text-sm font-semibold text-gray-700">
          Tools ({service.tools?.length ?? 0})
        </h2>
        <p className="text-xs text-gray-400 mt-0.5">Click a tool to expand and test it.</p>
      </div>

      <div className="space-y-3">
        {(service.tools ?? []).map((tool) => (
          <ToolTester key={tool.name} schemaId={id} tool={tool} />
        ))}
      </div>
    </div>
  );
}
