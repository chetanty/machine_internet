"use client";
import { useState } from "react";
import { Loader2, ChevronDown, ChevronUp } from "lucide-react";
import { api } from "@/lib/api";
import type { AgentTool, TestResult } from "@/types";

interface Props {
  schemaId: string;
  tool: AgentTool;
}

export default function ToolTester({ schemaId, tool }: Props) {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);

  async function run() {
    setLoading(true);
    setResult(null);
    try {
      const args: Record<string, unknown> = {};
      for (const p of tool.parameters) {
        const v = values[p.name];
        if (v === undefined || v === "") continue;
        if (p.type === "integer") args[p.name] = parseInt(v, 10);
        else if (p.type === "number") args[p.name] = parseFloat(v);
        else if (p.type === "boolean") args[p.name] = v === "true";
        else args[p.name] = v;
      }
      const res = await api.testTool(schemaId, tool.name, args) as TestResult;
      setResult(res);
    } catch (err: unknown) {
      setResult({ success: false, error: err instanceof Error ? err.message : String(err) });
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="border border-gray-200 rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-start justify-between p-4 bg-white hover:bg-gray-50 text-left"
      >
        <div>
          <p className="text-sm font-mono font-semibold text-gray-900">{tool.name}</p>
          <p className="text-xs text-gray-500 mt-0.5">{tool.description}</p>
        </div>
        <span className="ml-3 mt-0.5 text-gray-400">
          {open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </span>
      </button>

      {open && (
        <div className="border-t border-gray-200 bg-gray-50 p-4 space-y-3">
          {tool.parameters.length === 0 && (
            <p className="text-xs text-gray-400 italic">No parameters</p>
          )}

          {tool.parameters.map((p) => (
            <div key={p.name}>
              <label className="block text-xs font-medium text-gray-700 mb-1">
                {p.name}
                {p.required && <span className="text-red-500 ml-0.5">*</span>}
                <span className="ml-1 text-gray-400 font-normal">{p.type}</span>
              </label>
              {p.enum ? (
                <select
                  value={values[p.name] ?? ""}
                  onChange={(e) => setValues({ ...values, [p.name]: e.target.value })}
                  className="w-full border border-gray-300 rounded-md px-2 py-1.5 text-sm bg-white"
                >
                  <option value="">—</option>
                  {p.enum.map((v) => <option key={v}>{v}</option>)}
                </select>
              ) : (
                <input
                  type={p.type === "integer" || p.type === "number" ? "number" : "text"}
                  placeholder={p.description || p.name}
                  value={values[p.name] ?? ""}
                  onChange={(e) => setValues({ ...values, [p.name]: e.target.value })}
                  className="w-full border border-gray-300 rounded-md px-2 py-1.5 text-sm"
                />
              )}
              {p.description && (
                <p className="text-xs text-gray-400 mt-0.5">{p.description}</p>
              )}
            </div>
          ))}

          <button
            onClick={run}
            disabled={loading}
            className="flex items-center gap-2 bg-brand-500 hover:bg-brand-600 disabled:opacity-60 text-white text-xs font-medium px-3 py-1.5 rounded-lg transition-colors"
          >
            {loading && <Loader2 size={12} className="animate-spin" />}
            {loading ? "Running…" : "Run"}
          </button>

          {result && (
            <div className={`rounded-lg p-3 text-xs font-mono whitespace-pre-wrap overflow-auto max-h-64 ${
              result.success ? "bg-green-50 text-green-900" : "bg-red-50 text-red-800"
            }`}>
              {result.success
                ? JSON.stringify(result.result, null, 2)
                : result.error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
