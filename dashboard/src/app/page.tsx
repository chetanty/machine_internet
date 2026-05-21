"use client";
import { useEffect, useState } from "react";
import { Plus, RefreshCw } from "lucide-react";
import ServiceCard from "@/components/ServiceCard";
import AddServiceModal from "@/components/AddServiceModal";
import { api } from "@/lib/api";
import type { ServiceSummary, DiscoverResult } from "@/types";

export default function HomePage() {
  const [services, setServices] = useState<ServiceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const data = await api.listSchemas() as ServiceSummary[];
      setServices(data);
    } catch {
      /* backend may not be running yet */
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  function handleSuccess(_result: DiscoverResult) {
    setShowAdd(false);
    load();
  }

  function handleDelete(id: string) {
    setServices((s) => s.filter((x) => x.id !== id));
  }

  function handleUpdate(id: string, patch: Partial<ServiceSummary>) {
    setServices((s) => s.map((x) => (x.id === id ? { ...x, ...patch } : x)));
  }

  const active = services.filter((s) => s.serving).length;

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Services</h1>
          <p className="text-sm text-gray-500 mt-1">
            {services.length} wrapped &nbsp;·&nbsp; {active} serving
          </p>
        </div>
        <div className="flex gap-3">
          <button
            onClick={load}
            className="flex items-center gap-2 border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
          >
            <RefreshCw size={14} />
            Refresh
          </button>
          <button
            onClick={() => setShowAdd(true)}
            className="flex items-center gap-2 bg-brand-500 hover:bg-brand-600 text-white rounded-lg px-4 py-2 text-sm font-medium transition-colors"
          >
            <Plus size={16} />
            Add Service
          </button>
        </div>
      </div>

      {loading ? (
        <div className="text-sm text-gray-400 text-center py-20">Loading…</div>
      ) : services.length === 0 ? (
        <div className="text-center py-24 space-y-3">
          <p className="text-gray-500 text-sm">No services wrapped yet.</p>
          <button
            onClick={() => setShowAdd(true)}
            className="text-brand-600 text-sm font-medium hover:underline"
          >
            Wrap your first service →
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {services.map((s) => (
            <ServiceCard
              key={s.id}
              service={s}
              onDelete={handleDelete}
              onUpdate={handleUpdate}
            />
          ))}
        </div>
      )}

      {showAdd && (
        <AddServiceModal onClose={() => setShowAdd(false)} onSuccess={handleSuccess} />
      )}
    </div>
  );
}
