"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { getReport, ApiClientError } from "@/services/api";
import ReportCard from "@/components/ReportCard";
import type { ReportResponse } from "@/types/interview";

export default function ReportPage() {
  const params = useParams();
  const sessionId = params.sessionId as string;

  const [report, setReport] = useState<ReportResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let attempts = 0;
    const maxAttempts = 5;
    const delay = 1500;

    const tryFetch = async () => {
      try {
        const data = await getReport(sessionId);
        setReport(data);
        setLoading(false);
      } catch (err) {
        if (err instanceof ApiClientError && err.status === 409 && attempts < maxAttempts) {
          attempts++;
          setTimeout(tryFetch, delay);
        } else if (err instanceof ApiClientError) {
          setError(err.detail ?? err.message);
          setLoading(false);
        } else {
          setError("Failed to load report.");
          setLoading(false);
        }
      }
    };

    tryFetch();
  }, [sessionId]);

  if (loading) {
    return (
      <div className="text-center py-20">
        <div className="inline-block w-10 h-10 border-4 border-blue-200 border-t-blue-600 rounded-full animate-spin mb-4" />
        <p className="text-slate-500">Generating your evaluation…</p>
        <p className="text-xs text-slate-400 mt-1">This may take a few seconds</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-20">
        <h2 className="text-xl font-semibold text-slate-700 mb-2">
          Could not load report
        </h2>
        <p className="text-red-500 mb-6">{error}</p>
        <a
          href="/interview/start"
          className="bg-blue-600 text-white px-6 py-3 rounded-lg hover:bg-blue-700"
        >
          Start a new interview
        </a>
      </div>
    );
  }

  if (!report) return null;

  return (
    <div className="max-w-3xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-slate-900">Interview Report</h1>
        <a
          href="/interview/start"
          className="text-sm text-blue-600 hover:text-blue-700 font-medium"
        >
          + New Interview
        </a>
      </div>
      <ReportCard report={report} />
    </div>
  );
}
