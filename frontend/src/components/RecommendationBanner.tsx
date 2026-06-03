import type { SweepResults } from "../api/client";

interface Props {
  sweep: SweepResults | undefined;
}

export function RecommendationBanner({ sweep }: Props) {
  if (!sweep) {
    return (
      <div className="bg-yellow-900/40 border border-yellow-600 rounded-lg p-4 mb-6">
        <p className="text-yellow-300 text-sm">
          ⚠ Sweep results not available. Run{" "}
          <code className="bg-yellow-900/60 px-1 rounded">python scripts/run_sweep.py</code> to
          generate a multi-model comparison and recommendation.
        </p>
      </div>
    );
  }

  const { recommendation, model_summaries, metadata } = sweep;
  const frontier = model_summaries.find((m) => m.model === recommendation.frontier_baseline);
  const modelA = model_summaries.find((m) => m.model === recommendation.model_a);

  const savingsPct = recommendation.cost_savings_vs_frontier_pct.toFixed(1);
  const accuracyDelta = recommendation.accuracy_delta_vs_frontier_pct.toFixed(1);
  const sign = parseFloat(accuracyDelta) >= 0 ? "+" : "";

  return (
    <div className="bg-green-950 border-2 border-green-500 rounded-xl p-5 mb-6 shadow-lg">
      <div className="flex items-start gap-3">
        <span className="text-2xl">✅</span>
        <div className="flex-1">
          <h2 className="text-lg font-bold text-green-300 mb-1">
            Production Recommendation — Based on{" "}
            {metadata.models_evaluated.length} models × {metadata.total_issues} issues
          </h2>
          <p className="text-green-100 text-base mb-3">
            <strong>Run `{recommendation.model_a}` in production.</strong>
          </p>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
            <div className="bg-green-900/50 rounded-lg p-3 text-center">
              <div className="text-xl font-bold text-green-300">
                {savingsPct}% cheaper
              </div>
              <div className="text-xs text-green-400">cost per call vs frontier</div>
            </div>
            <div className="bg-green-900/50 rounded-lg p-3 text-center">
              <div className="text-xl font-bold text-green-300">
                {sign}{accuracyDelta}%
              </div>
              <div className="text-xs text-green-400">accuracy vs frontier</div>
            </div>
            {modelA && (
              <div className="bg-green-900/50 rounded-lg p-3 text-center">
                <div className="text-xl font-bold text-green-300">
                  ${modelA.cost_per_correct_classification.toFixed(5)}
                </div>
                <div className="text-xs text-green-400">cost per correct answer</div>
              </div>
            )}
            {frontier && (
              <div className="bg-red-950/50 rounded-lg p-3 text-center">
                <div className="text-xl font-bold text-red-300">
                  ${frontier.cost_per_correct_classification.toFixed(5)}
                </div>
                <div className="text-xs text-red-400">frontier cost per correct</div>
              </div>
            )}
          </div>
          <p className="text-green-200 text-sm mb-2">{recommendation.rationale}</p>
          <p className="text-green-400 text-xs">
            📋 Production pattern: Use{" "}
            <strong>{recommendation.model_a}</strong> as primary classifier. Route
            predicted-<em>security</em> and uncertain cases to human review or the frontier
            as a fallback.
          </p>
        </div>
      </div>
    </div>
  );
}
