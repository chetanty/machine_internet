"use client";
import { useState } from "react";
import { X, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { DiscoverResult } from "@/types";

interface Props {
  onClose: () => void;
  onSuccess: (result: DiscoverResult) => void;
}

export default function AddServiceModal({ onClose, onSuccess }: Props) {
  const [url, setUrl] = useState("");
  const [forceTraffic, setForceTraffic] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    setLoading(true);
    setError("");
    setStatus("Discovering…");

    try {
      const result = await api.discover(url.trim(), forceTraffic) as DiscoverResult;
      setStatus(`Found ${result.tool_count} tools via ${result.discovery_method}`);
      setTimeout(() => onSuccess(result), 800);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Discovery failed");
      setStatus("");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg mx-4 p-6">
        <div className="flex items-center justify-between mb-5">
          <h2 className="font-semibold text-gray-900">Add Service</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Service URL
            </label>
            <input
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://api.example.com"
              required
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
          </div>

          <label className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={forceTraffic}
              onChange={(e) => setForceTraffic(e.target.checked)}
              className="rounded"
            />
            Force traffic sniffing (skip OpenAPI check)
          </label>

          {status && (
            <p className="text-sm text-brand-600 flex items-center gap-2">
              {loading && <Loader2 size={14} className="animate-spin" />}
              {status}
            </p>
          )}
          {error && <p className="text-sm text-red-600">{error}</p>}

          <div className="flex gap-3 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 border border-gray-300 rounded-lg py-2 text-sm text-gray-700 hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading}
              className="flex-1 bg-brand-500 hover:bg-brand-600 disabled:opacity-60 text-white rounded-lg py-2 text-sm font-medium transition-colors"
            >
              {loading ? "Discovering…" : "Discover"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
