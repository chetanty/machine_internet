import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Universal Agent Adapter",
  description: "Turn any service into an MCP tool in seconds",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <header className="border-b border-gray-200 bg-white px-6 py-4 flex items-center gap-3">
          <div className="w-7 h-7 rounded-lg bg-brand-500 flex items-center justify-center">
            <span className="text-white text-xs font-bold">U</span>
          </div>
          <span className="font-semibold text-gray-900 text-sm tracking-tight">
            Universal Agent Adapter
          </span>
          <span className="ml-auto text-xs text-gray-400">v1</span>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
