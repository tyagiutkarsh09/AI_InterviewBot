"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { submitAnswer, ApiClientError } from "@/services/api";
import ProgressBar from "@/components/ProgressBar";
import type { StartInterviewResponse, SubmitAnswerResponse } from "@/types/interview";

interface InterviewState {
  sessionId: string;
  candidateName: string;
  currentQuestion: string;
  questionNumber: number;
  totalQuestions: number;
  topic: string;
  lastScore: number | null;
  lastReasoning: string | null;
  lastFeedback: string | null;
  answeredCount: number;
}

const SESSION_STORAGE_KEY = (id: string) => `interview_session_${id}`;

export default function InterviewPage() {
  const params = useParams();
  const router = useRouter();
  const sessionId = params.sessionId as string;

  const [state, setState] = useState<InterviewState | null>(null);
  const [answer, setAnswer] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [showFeedback, setShowFeedback] = useState(false);
  const timerRef = useRef<NodeJS.Timeout | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const raw = sessionStorage.getItem(SESSION_STORAGE_KEY(sessionId));
    if (!raw) {
      setState(null);
      return;
    }
    const init = JSON.parse(raw) as StartInterviewResponse;
    setState({
      sessionId,
      candidateName: init.candidate_name,
      currentQuestion: init.question_text,
      questionNumber: init.question_number,
      totalQuestions: init.total_questions,
      topic: init.topic,
      lastScore: null,
      lastReasoning: null,
      lastFeedback: null,
      answeredCount: 0,
    });
    textareaRef.current?.focus();
  }, [sessionId]);

  useEffect(() => {
    timerRef.current = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  useEffect(() => {
    setElapsed(0);
    textareaRef.current?.focus();
  }, [state?.questionNumber]);

  const formatTime = (secs: number) => {
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${m}:${s.toString().padStart(2, "0")}`;
  };

  const handleSubmit = useCallback(async () => {
    if (!state || !answer.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    setShowFeedback(false);

    try {
      const res = await submitAnswer({ session_id: sessionId, answer: answer.trim() });

      if (res.is_complete) {
        sessionStorage.removeItem(SESSION_STORAGE_KEY(sessionId));
        router.push(`/report/${sessionId}`);
        return;
      }

      setState((prev) =>
        prev
          ? {
              ...prev,
              currentQuestion: res.next_question ?? prev.currentQuestion,
              questionNumber: res.question_number ?? prev.questionNumber,
              totalQuestions: res.total_questions ?? prev.totalQuestions,
              topic: res.topic ?? prev.topic,
              lastScore: res.score,
              lastReasoning: res.score_reasoning,
              lastFeedback: null,
              answeredCount: prev.answeredCount + 1,
            }
          : prev
      );
      setAnswer("");
      setShowFeedback(true);
    } catch (err) {
      if (err instanceof ApiClientError) {
        setError(err.detail ?? err.message);
      } else {
        setError("Submission failed. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }, [state, answer, submitting, sessionId, router]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      handleSubmit();
    }
  };

  if (state === null) {
    return (
      <div className="text-center py-20">
        <h2 className="text-xl font-semibold text-slate-700 mb-2">
          Session not found
        </h2>
        <p className="text-slate-500 mb-6">
          This session has expired or does not exist.
        </p>
        <a
          href="/interview/start"
          className="bg-blue-600 text-white px-6 py-3 rounded-lg hover:bg-blue-700"
        >
          Start a new interview
        </a>
      </div>
    );
  }

  const timerColor =
    elapsed > 120
      ? "text-red-600"
      : elapsed > 60
      ? "text-orange-500"
      : "text-slate-500";

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <span className="text-sm text-slate-500">
          Interviewing {state.candidateName}
        </span>
        <span className={`text-sm font-mono font-medium ${timerColor}`}>
          {formatTime(elapsed)}
        </span>
      </div>

      <ProgressBar
        current={state.questionNumber}
        total={state.totalQuestions}
        topic={state.topic}
      />

      {showFeedback && state.lastScore !== null && (
        <div className="bg-blue-50 border border-blue-200 rounded-xl p-4 text-sm">
          <div className="flex items-center gap-2 mb-1">
            <span className="font-semibold text-blue-800">
              Score: {state.lastScore.toFixed(1)}/10
            </span>
          </div>
          {state.lastReasoning && (
            <p className="text-blue-700">{state.lastReasoning}</p>
          )}
        </div>
      )}

      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
        <div className="flex items-start gap-3 mb-6">
          <div className="w-8 h-8 rounded-full bg-blue-600 flex items-center justify-center text-white text-sm font-bold flex-shrink-0">
            AI
          </div>
          <div>
            <p className="text-xs text-slate-400 mb-1">Interviewer</p>
            <p className="text-slate-900 text-base leading-relaxed">
              {state.currentQuestion}
            </p>
          </div>
        </div>

        <div>
          <label className="text-xs text-slate-400 uppercase tracking-wide font-medium mb-2 block">
            Your Answer
          </label>
          <textarea
            ref={textareaRef}
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type your answer here… (Ctrl+Enter to submit)"
            rows={6}
            disabled={submitting}
            className="w-full border border-slate-200 rounded-xl px-4 py-3 text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none disabled:opacity-50"
          />
          <div className="flex items-center justify-between mt-3">
            <span className="text-xs text-slate-400">
              {answer.length > 0 ? `${answer.split(/\s+/).filter(Boolean).length} words` : ""}
            </span>
            <button
              onClick={handleSubmit}
              disabled={submitting || !answer.trim()}
              className="bg-blue-600 hover:bg-blue-700 disabled:bg-slate-300 text-white font-semibold px-6 py-2.5 rounded-xl text-sm transition-colors"
            >
              {submitting ? "Evaluating…" : "Submit Answer →"}
            </button>
          </div>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
          {error}
        </div>
      )}

      <p className="text-xs text-slate-400 text-center">
        {state.questionNumber} of {state.totalQuestions} questions ·{" "}
        {state.totalQuestions - state.questionNumber} remaining
      </p>
    </div>
  );
}
