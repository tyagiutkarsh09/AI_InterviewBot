"use client";

import ScoreBadge from "./ScoreBadge";
import type { ReportResponse } from "@/types/interview";

const RECOMMENDATION_LABELS: Record<string, { label: string; color: string }> = {
  strong_yes: { label: "Strong Yes", color: "bg-green-100 text-green-800 border-green-200" },
  yes: { label: "Yes", color: "bg-blue-100 text-blue-800 border-blue-200" },
  no: { label: "No", color: "bg-orange-100 text-orange-800 border-orange-200" },
  strong_no: { label: "Strong No", color: "bg-red-100 text-red-800 border-red-200" },
};

interface ReportCardProps {
  report: ReportResponse;
}

export default function ReportCard({ report }: ReportCardProps) {
  const rec = RECOMMENDATION_LABELS[report.recommendation] ?? {
    label: report.recommendation,
    color: "bg-slate-100 text-slate-800 border-slate-200",
  };

  const durationStr = report.duration_seconds
    ? `${Math.floor(report.duration_seconds / 60)}m ${report.duration_seconds % 60}s`
    : null;

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-4">
          <div>
            <h2 className="text-2xl font-bold text-slate-900">
              {report.candidate_name}
            </h2>
            <p className="text-slate-500">
              {report.job_role} · {report.experience_level}
              {durationStr && ` · ${durationStr}`}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <ScoreBadge score={report.overall_score} size="lg" />
            <span
              className={`text-sm font-semibold px-3 py-1.5 rounded-full border ${rec.color}`}
            >
              {rec.label}
            </span>
          </div>
        </div>
        <p className="text-slate-700 text-sm leading-relaxed bg-slate-50 rounded-lg p-4">
          {report.summary}
        </p>
      </div>

      <div className="grid sm:grid-cols-2 gap-4">
        <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
          <h3 className="font-semibold text-green-700 mb-3">✓ Strengths</h3>
          <ul className="space-y-2">
            {report.strengths.map((s, i) => (
              <li key={i} className="text-sm text-slate-700 flex gap-2">
                <span className="text-green-500 mt-0.5">•</span>
                {s}
              </li>
            ))}
          </ul>
        </div>
        <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
          <h3 className="font-semibold text-orange-700 mb-3">
            ↑ Areas to Improve
          </h3>
          <ul className="space-y-2">
            {report.weaknesses.map((w, i) => (
              <li key={i} className="text-sm text-slate-700 flex gap-2">
                <span className="text-orange-400 mt-0.5">•</span>
                {w}
              </li>
            ))}
          </ul>
        </div>
      </div>

      {Object.keys(report.topic_scores).length > 0 && (
        <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
          <h3 className="font-semibold text-slate-900 mb-4">
            Scores by Topic
          </h3>
          <div className="space-y-3">
            {Object.entries(report.topic_scores).map(([topic, score]) => (
              <div key={topic} className="flex items-center gap-3">
                <span className="text-sm text-slate-600 w-32 capitalize">
                  {topic.replace(/_/g, " ")}
                </span>
                <div className="flex-1 h-2.5 bg-slate-100 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      score >= 8
                        ? "bg-green-500"
                        : score >= 6
                        ? "bg-blue-500"
                        : score >= 4
                        ? "bg-yellow-500"
                        : "bg-red-400"
                    }`}
                    style={{ width: `${(score / 10) * 100}%` }}
                  />
                </div>
                <span className="text-sm font-medium text-slate-700 w-10 text-right">
                  {score.toFixed(1)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
        <h3 className="font-semibold text-slate-900 mb-4">Question Breakdown</h3>
        <div className="space-y-4">
          {report.per_question.map((q, i) => (
            <div
              key={q.question_id}
              className="border border-slate-100 rounded-lg p-4"
            >
              <div className="flex items-start justify-between gap-3 mb-2">
                <p className="text-sm font-medium text-slate-800">
                  Q{i + 1}: {q.question_text}
                </p>
                {q.score !== null && (
                  <ScoreBadge score={q.score} size="sm" />
                )}
              </div>
              <p className="text-xs text-slate-500 mb-2 capitalize">
                Topic: {q.topic.replace(/_/g, " ")}
              </p>
              {q.answer_text && (
                <div className="bg-slate-50 rounded p-2 mb-2">
                  <p className="text-xs text-slate-400 mb-1">Your answer:</p>
                  <p className="text-sm text-slate-700 line-clamp-3">
                    {q.answer_text}
                  </p>
                </div>
              )}
              {q.score_reasoning && (
                <p className="text-xs text-slate-500 italic">
                  {q.score_reasoning}
                </p>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
