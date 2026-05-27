"use client";

interface ProgressBarProps {
  current: number;
  total: number;
  topic?: string;
}

export default function ProgressBar({ current, total, topic }: ProgressBarProps) {
  const pct = total > 0 ? Math.round((current / total) * 100) : 0;

  return (
    <div className="w-full">
      <div className="flex items-center justify-between text-sm text-slate-500 mb-1.5">
        <span>
          Question {current} of {total}
          {topic && (
            <span className="ml-2 bg-slate-100 text-slate-600 px-2 py-0.5 rounded-full text-xs">
              {topic.replace(/_/g, " ")}
            </span>
          )}
        </span>
        <span>{pct}%</span>
      </div>
      <div className="w-full h-2 bg-slate-200 rounded-full overflow-hidden">
        <div
          className="h-full bg-blue-500 rounded-full transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
