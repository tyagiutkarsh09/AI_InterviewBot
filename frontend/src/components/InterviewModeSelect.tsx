"use client";

import Link from "next/link";

interface ModeCardProps {
  icon: string;
  title: string;
  description: string;
  bullets: string[];
  href: string;
  cta: string;
  accent: "blue" | "violet";
}

function ModeCard({ icon, title, description, bullets, href, cta, accent }: ModeCardProps) {
  const accentClasses = {
    blue: {
      border: "hover:border-blue-400",
      iconBg: "bg-blue-50",
      iconText: "text-blue-600",
      bullet: "text-blue-500",
      btn: "bg-blue-600 hover:bg-blue-700",
    },
    violet: {
      border: "hover:border-violet-400",
      iconBg: "bg-violet-50",
      iconText: "text-violet-600",
      bullet: "text-violet-500",
      btn: "bg-violet-600 hover:bg-violet-700",
    },
  }[accent];

  return (
    <div
      className={`bg-white rounded-2xl border-2 border-slate-200 ${accentClasses.border} shadow-sm p-8 flex flex-col transition-all duration-200 hover:shadow-md`}
    >
      <div className={`w-14 h-14 rounded-xl ${accentClasses.iconBg} flex items-center justify-center text-3xl mb-5`}>
        {icon}
      </div>
      <h2 className="text-xl font-bold text-slate-900 mb-2">{title}</h2>
      <p className="text-slate-500 text-sm mb-5 leading-relaxed">{description}</p>
      <ul className="space-y-2 mb-8 flex-1">
        {bullets.map((b) => (
          <li key={b} className="flex items-start gap-2 text-sm text-slate-600">
            <span className={`mt-0.5 ${accentClasses.bullet} font-bold`}>✓</span>
            {b}
          </li>
        ))}
      </ul>
      <Link
        href={href}
        className={`${accentClasses.btn} text-white font-semibold py-3 px-6 rounded-xl text-sm text-center transition-colors`}
      >
        {cta}
      </Link>
    </div>
  );
}

export default function InterviewModeSelect() {
  return (
    <div className="max-w-3xl mx-auto">
      <div className="text-center mb-10">
        <h1 className="text-3xl font-bold text-slate-900 mb-3">Choose Interview Mode</h1>
        <p className="text-slate-500 text-base">
          Select how you&apos;d like to conduct your interview session.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
        <ModeCard
          icon="💬"
          title="Text Interview"
          description="Type your answers at your own pace. Great for practicing written communication and technical explanations."
          bullets={[
            "Type answers in your own words",
            "Review and edit before submitting",
            "Instant AI scoring and feedback",
            "Detailed report at the end",
          ]}
          href="/interview/start"
          cta="Start Text Interview →"
          accent="blue"
        />
        <ModeCard
          icon="🎙"
          title="Voice Interview"
          description="Speak your answers in real-time with live transcription. Simulates a real phone or video interview experience."
          bullets={[
            "Speak naturally, mic auto-detects voice",
            "Live transcript as you talk",
            "AI responds with synthesized speech",
            "Barge-in to interrupt the bot",
          ]}
          href="/interview/voice/start"
          cta="Start Voice Interview →"
          accent="violet"
        />
      </div>

      <p className="text-center text-xs text-slate-400 mt-8">
        Both modes use the same AI interviewer and scoring rubric.
      </p>
    </div>
  );
}
