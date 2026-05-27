import Link from "next/link";

export default function HomePage() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[70vh] text-center">
      <div className="max-w-2xl">
        <h1 className="text-4xl font-bold text-slate-900 mb-4">
          AI-Powered Technical Interviews
        </h1>
        <p className="text-lg text-slate-600 mb-8">
          Practice realistic technical interviews with instant AI feedback.
          Get scored on your answers and receive detailed improvement tips.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-10 text-left">
          <FeatureCard
            icon="❓"
            title="Smart Questions"
            description="Role-specific questions tailored to your experience level"
          />
          <FeatureCard
            icon="🤖"
            title="AI Evaluation"
            description="Claude evaluates every answer with a score and reasoning"
          />
          <FeatureCard
            icon="📊"
            title="Detailed Report"
            description="Full scorecard with strengths, weaknesses, and recommendations"
          />
        </div>
        <Link
          href="/interview/mode-select"
          className="inline-block bg-blue-600 hover:bg-blue-700 text-white font-semibold px-8 py-4 rounded-xl text-lg transition-colors"
        >
          Start an Interview →
        </Link>
      </div>
    </div>
  );
}

function FeatureCard({
  icon,
  title,
  description,
}: {
  icon: string;
  title: string;
  description: string;
}) {
  return (
    <div className="bg-white rounded-xl p-5 border border-slate-200 shadow-sm">
      <div className="text-2xl mb-2">{icon}</div>
      <h3 className="font-semibold text-slate-900 mb-1">{title}</h3>
      <p className="text-sm text-slate-500">{description}</p>
    </div>
  );
}
