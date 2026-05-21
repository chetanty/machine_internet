const BASE = "/backend";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(body || res.statusText);
  }
  return res.json() as Promise<T>;
}

export const api = {
  discover: (url: string, forceTraffic = false) =>
    request("/api/discover", {
      method: "POST",
      body: JSON.stringify({ url, force_traffic: forceTraffic }),
    }),

  listSchemas: () => request("/api/schemas"),

  getSchema: (id: string) => request(`/api/schemas/${id}`),

  deleteSchema: (id: string) =>
    request(`/api/schemas/${id}`, { method: "DELETE" }),

  startServing: (id: string) =>
    request(`/api/schemas/${id}/serve`, { method: "POST" }),

  stopServing: (id: string) =>
    request(`/api/schemas/${id}/stop`, { method: "POST" }),

  testTool: (schemaId: string, toolName: string, args: Record<string, unknown>) =>
    request("/api/tools/test", {
      method: "POST",
      body: JSON.stringify({ schema_id: schemaId, tool_name: toolName, arguments: args }),
    }),

  storeCredentials: (serviceName: string, credentials: Record<string, string>) =>
    request("/api/credentials", {
      method: "POST",
      body: JSON.stringify({ service_name: serviceName, credentials }),
    }),
};
