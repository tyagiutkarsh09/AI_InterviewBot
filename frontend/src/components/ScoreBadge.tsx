"use client";

interface ScoreBadgeProps {
  score: number;
  size?: "sm" | "md" | "lg";
}

export default function ScoreBadge({ score, size = "md" }: ScoreBadgeProps) {
  const color =
    score >= 8
      ? "bg-green-100 text-green-700 border-green-200"
      : score >= 6
      ? "bg-blue-100 text-blue-700 border-blue-200"
      : score >= 4
      ? "bg-yellow-100 text-yellow-700 border-yellow-200"
      : "bg-red-100 text-red-700 border-red-200";

  const sizeClasses =
    size === "lg"
      ? "text-2xl px-4 py-2"
      : size === "sm"
      ? "text-xs px-2 py-0.5"
      : "text-sm px-3 py-1";

  return (
    <span
      className={`inline-flex items-center font-bold rounded-full border ${color} ${sizeClasses}`}
    >
      {score.toFixed(1)}/10
    </span>
  );
}
