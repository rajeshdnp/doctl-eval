/**
 * App.tsx — top-level layout.
 *
 * Structure:
 * 1. Recommendation banner (always visible, loads from /api/sweep)
 * 2. Model selectors + concurrency input + Run button
 * 3. SSE progress bar during run
 * 4. Tabs: Scored View | Unscored View | Sweep Overview
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, startRun } from "./api/client";
import type { SSEEvent, RunResults } from "./api/client";
import { RecommendationBanner } from "./components/RecommendationBanner";
import { ScoredView } from "./components/ScoredView";
import { UnscoredView } from "./components/UnscoredView";
import { SweepOverview } from "./components/SweepOverview";

type Tab = "scored" | "unscored" | "sweep";

export default function App() {
  const [tab, setTab] = useState<Tab>("scored");
  const [modelA, setModelA] = useState("");
  const [modelB, setModelB] = useState("");
  const [concurrency, setConcurrency] = useState<number>(10);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<{ model: string; completed: number; total: number; cost: number } | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [runResults, setRunResults] = useState<RunResults | null>(null);

  // Data queries
  const { data: sweep } = useQuery({
    queryKey: ["sweep"],
    queryFn: api.sweep,
    retry: false,
  });

  const { data: models } = useQuery({
    queryKey: ["models"],
    queryFn: api.models,
  });

  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    refetchInterval: 60_000,
  });

  // Default model selections from sweep recommendation
  if (models && !modelA && !modelB) {
    const defaultA = sweep?.recommendation.model_a ?? models[0]?.slug ?? "";
    const defaultB = sweep?.recommendation.model_b ?? models[1]?.slug ?? "";
    if (defaultA) setModelA(defaultA);
    if (defaultB) setModelB(defaultB);
  }

  async function handleRun() {
    if (!modelA || !modelB) return;
    setRunning(true);
    setRunError(null);
    setProgress(null);
    setRunResults(null);

    try {
      const runId = await startRun(modelA, modelB, concurrency, (event: SSEEvent) => {
        if (event.type === "progress") {
          setProgress({
            model: event.model,
            completed: event.completed,
            total: event.total,
            cost: event.current_cost,
          });
        }
        if (event.type === "error") {
          setRunError(event.message);
        }
      });

      setRunId(runId);

      // Fetch final results
      const results = await api.results(runId);
      setRunResults(results);
    } catch (err: unknown) {
      setRunError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
      setProgress(null);
    }
  }

  const tabClass = (t: Tab) =>
    `px-4 py-2 text-sm font-medium rounded-t-lg transition-colors ${
      tab === t
        ? "bg-gray-800 text-white border-b-2 border-indigo-500"
        : "text-gray-400 hover:text-gray-200 hover:bg-gray-800/50"
    }`;

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white mb-1">
          doctl-eval — LLM Issue Classification Evaluation
        </h1>
        {health && (
          <p className="text-sm text-gray-500">
            Corpus: {health.corpus_size} issues ({health.scored_size} scored)
            {health.sweep_available ? " · sweep available" : " · no sweep yet"}
          </p>
        )}
      </div>

      {/* Recommendation banner — always visible, top of page */}
      <RecommendationBanner sweep={sweep} />

      {/* Controls */}
      <div className="bg-gray-900 rounded-xl p-4 mb-6 flex flex-wrap items-end gap-4">
        <div>
          <label className="block text-xs text-gray-400 mb-1">Model A</label>
          <select
            className="bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 min-w-48"
            value={modelA}
            onChange={(e) => setModelA(e.target.value)}
          >
            <option value="">Select model A…</option>
            {models?.map((m) => (
              <option key={m.slug} value={m.slug}>
                {m.display_name}{" "}
                {m.role === "frontier_baseline" ? "(frontier)" : ""}
                {m.role === "open_source" ? "(recommended)" : ""}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs text-gray-400 mb-1">Model B</label>
          <select
            className="bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 min-w-48"
            value={modelB}
            onChange={(e) => setModelB(e.target.value)}
          >
            <option value="">Select model B…</option>
            {models?.map((m) => (
              <option key={m.slug} value={m.slug}>
                {m.display_name}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs text-gray-400 mb-1">Concurrency</label>
          <input
            type="number"
            min={1}
            max={50}
            value={concurrency}
            onChange={(e) => setConcurrency(parseInt(e.target.value) || 10)}
            className="bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 w-24"
          />
        </div>

        <button
          onClick={handleRun}
          disabled={running || !modelA || !modelB}
          className={`px-5 py-2 rounded-lg font-semibold text-sm transition-colors ${
            running || !modelA || !modelB
              ? "bg-gray-700 text-gray-500 cursor-not-allowed"
              : "bg-indigo-600 hover:bg-indigo-500 text-white"
          }`}
        >
          {running ? "Running…" : "Run Evaluation"}
        </button>

        {runId && !running && (
          <span className="text-xs text-gray-500">run: {runId}</span>
        )}
      </div>

      {/* Progress bar */}
      {running && progress && (
        <div className="mb-4">
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>{progress.model}: {progress.completed}/{progress.total}</span>
            <span>cost so far: ${progress.cost.toFixed(5)}</span>
          </div>
          <div className="h-2 bg-gray-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-indigo-500 transition-all duration-300"
              style={{
                width: `${Math.min(100, (progress.completed / progress.total) * 100)}%`,
              }}
            />
          </div>
        </div>
      )}

      {/* Error */}
      {runError && (
        <div className="bg-red-950 border border-red-700 rounded-lg p-3 mb-4 text-sm text-red-300">
          Run failed: {runError}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 mb-0 border-b border-gray-700">
        <button className={tabClass("scored")} onClick={() => setTab("scored")}>
          Scored View
        </button>
        <button className={tabClass("unscored")} onClick={() => setTab("unscored")}>
          Unscored View
        </button>
        <button className={tabClass("sweep")} onClick={() => setTab("sweep")}>
          Sweep Overview
        </button>
      </div>

      {/* Tab content */}
      <div className="bg-gray-900 rounded-b-xl rounded-tr-xl p-5">
        {tab === "scored" && (
          <>
            {runResults ? (
              <ScoredView results={runResults} />
            ) : (
              <div className="text-center py-12 text-gray-500">
                <p className="text-lg mb-2">No run results yet.</p>
                <p className="text-sm">
                  Select two models above and click "Run Evaluation" to start.
                  If models were previously run, results are cached ($0 cost).
                </p>
              </div>
            )}
          </>
        )}

        {tab === "unscored" && (
          <>
            {runResults ? (
              <UnscoredView results={runResults} />
            ) : (
              <div className="text-center py-12 text-gray-500">
                Run an evaluation to see unscored predictions.
              </div>
            )}
          </>
        )}

        {tab === "sweep" && (
          <>
            {sweep ? (
              <SweepOverview sweep={sweep} />
            ) : (
              <div className="text-center py-12 text-gray-500">
                <p className="text-lg mb-2">No sweep results yet.</p>
                <p className="text-sm">
                  Run{" "}
                  <code className="bg-gray-800 px-1 rounded">python scripts/run_sweep.py</code> to
                  generate multi-model comparison data.
                </p>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
