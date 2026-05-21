export interface ToolParameter {
  name: string;
  type: string;
  required: boolean;
  description: string;
  enum?: string[];
}

export interface AgentTool {
  name: string;
  description: string;
  parameters: ToolParameter[];
}

export interface ServiceSummary {
  id: string;
  service_name: string;
  service_description: string;
  tool_count: number;
  auth_type: string;
  source_url: string;
  created_at: string;
  serving: boolean;
  serve_port: number | null;
}

export interface ServiceDetail extends ServiceSummary {
  base_url: string;
  tools: AgentTool[];
}

export interface DiscoverResult {
  service_name: string;
  tool_count: number;
  tools: { name: string; description: string }[];
  schema_file: string;
  discovery_method: string;
}

export interface TestResult {
  success: boolean;
  result?: unknown;
  error?: string;
}
