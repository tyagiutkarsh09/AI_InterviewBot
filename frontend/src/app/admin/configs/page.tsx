"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import AdminGuard from "@/components/AdminGuard";
import { listConfigs } from "@/services/admin-api";
import type { ConfigResponse } from "@/types/admin";

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  try {
    return new Date(iso).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export default function ConfigsPage() {
  const [configs, setConfigs] = useState<ConfigResponse[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    setLoading(true);
    setError("");
    listConfigs()
      .then((data) => {
        setConfigs(data.configs);
        setTotal(data.total);
      })
      .catch((err) => {
        setError(err.detail ?? err.message ?? "Failed to load configs.");
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <AdminGuard>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-900">Interview Configs</h1>
            <p className="text-sm text-slate-500 mt-1">
              {total} config{total !== 1 ? "s" : ""} on record
            </p>
          </div>
          <Link
            href="/admin/configs/new"
            className="bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold px-4 py-2 rounded-lg transition-colors"
          >
            Create new
          </Link>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700">
            {error}
          </div>
        )}

        {loading ? (
          <div className="bg-white rounded-xl border border-slate-200 p-12 text-center">
            <p className="text-slate-500 text-sm">Loading configs...</p>
          </div>
        ) : configs.length === 0 ? (
          <div className="bg-white rounded-xl border border-slate-200 p-12 text-center">
            <p className="text-slate-500">No configs yet.</p>
            <p className="text-sm text-slate-400 mt-1">
              Create one from a job description to get started.
            </p>
          </div>
        ) : (
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 bg-slate-50">
                    <th className="text-left px-4 py-3 font-medium text-slate-600">Title</th>
                    <th className="text-left px-4 py-3 font-medium text-slate-600">Role</th>
                    <th className="text-left px-4 py-3 font-medium text-slate-600">Level</th>
                    <th className="text-left px-4 py-3 font-medium text-slate-600">Questions</th>
                    <th className="text-left px-4 py-3 font-medium text-slate-600">Created</th>
                    <th className="text-right px-4 py-3 font-medium text-slate-600"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {configs.map((config) => (
                    <tr key={config.id} className="hover:bg-slate-50 transition-colors">
                      <td className="px-4 py-3 font-medium text-slate-900">{config.title}</td>
                      <td className="px-4 py-3 text-slate-600">{config.role}</td>
                      <td className="px-4 py-3 text-slate-600 capitalize">{config.experience_level}</td>
                      <td className="px-4 py-3 text-slate-600">{config.total_questions}</td>
                      <td className="px-4 py-3 text-slate-500">{formatDate(config.created_at)}</td>
                      <td className="px-4 py-3 text-right">
                        <Link
                          href={`/interview/start?configId=${config.id}`}
                          className="text-sm font-medium text-blue-600 hover:text-blue-700 transition-colors"
                        >
                          Start interview
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </AdminGuard>
  );
}
