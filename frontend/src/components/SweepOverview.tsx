/**
 * Sweep overview — 4-5 model comparison table + cost savings chart.
 * Sorted by cost_per_correct_classification (the business-relevant sort).
 */
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Cell,
  ResponsiveContainer,
} from "recharts";
import type { SweepResults } from "../api/client";

const MONTHLY_VOLUMES = [100_000, 1_000_000, 10_000_000];

interface Props {
  sweep: SweepResults;
}

export function SweepOverview({ sweep }: Props) {
  const { model_summaries: summaries, recommendation: rec, metadata } = sweep;

  // Cost extrapolation uses avg_cost_usd
  function monthlyCost(avgCostPerCall: number, volume: number) {
    return (avgCostPerCall * volume).toFixed(2);
  }

  const frontier = summaries.find((m) => m.model === rec.frontier_baseline);

  const costChartData = summaries.map((m) => ({
    model: m.model.split(/[-_]/)[0] + (m.model.includes("70b") ? " 70B" : m.model.includes("120b") ? " 120B" : m.model.includes("20b") ? " 20B" : ""),
    cost_per_correct: m.cost_per_correct_classification > 999 ? null : m.cost_per_correct_classification,
    isRecommended: m.model === rec.model_a,
    isFrontier: m.model === rec.frontier_baseline,
  }));

  function roleLabel(slug: string) {
    if (slug === rec.model_a) return "✅ Rec A";
    if (slug === rec.model_b) return "🔵 Rec B";
    if (slug === rec.frontier_baseline) return "🔴 Frontier";
    return "";
  }

  return (
    <div>
      {/* Header */}
      <div className="mb-4">
        <h2 className="text-base font-semibold text-gray-300">
          {metadata.models_evaluated.length} models evaluated × {metadata.total_issues} issues (
          {metadata.scored_issues} scored) — sorted by cost/correct ↑
        </h2>
        <p className="text-sm text-gray-500 mt-1">
          Sweep date: {new Date(metadata.sweep_date).toLocaleDateString()}
        </p>
      </div>

      {/* Model comparison table */}
      <div className="overflow-x-auto mb-6">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left border-b border-gray-700">
              <th className="py-2 pr-3 text-gray-400">Model</th>
              <th className="py-2 pr-3 text-gray-400 text-right">Accuracy</th>
              <th className="py-2 pr-3 text-gray-400 text-right">Macro-F1</th>
              <th className="py-2 pr-3 text-gray-400 text-right">Avg Cost/Call</th>
              <th className="py-2 pr-3 text-yellow-400 text-right">Cost/Correct ⭐</th>
              <th className="py-2 pr-3 text-gray-400 text-right">p50ms</th>
              <th className="py-2 pr-3 text-gray-400 text-right">p95ms</th>
              <th className="py-2 text-gray-400 text-right">Error%</th>
              <th className="py-2 pl-3 text-gray-400">Role</th>
            </tr>
          </thead>
          <tbody>
            {summaries.map((m) => {
              const isRec = m.model === rec.model_a;
              const isFrontier = m.model === rec.frontier_baseline;
              const rowClass = isRec
                ? "bg-green-950/30 border-green-800"
                : isFrontier
                ? "bg-red-950/30 border-red-900"
                : "";

              return (
                <tr key={m.model} className={`border-b ${rowClass || "border-gray-800"}`}>
                  <td className="py-2 pr-3">
                    <span className={isRec ? "text-green-300 font-medium" : isFrontier ? "text-red-300" : "text-gray-300"}>
                      {m.model}
                    </span>
                  </td>
                  <td className="py-2 pr-3 text-right text-gray-300">{(m.accuracy * 100).toFixed(1)}%</td>
                  <td className="py-2 pr-3 text-right text-gray-300">{m.macro_f1.toFixed(3)}</td>
                  <td className="py-2 pr-3 text-right text-gray-400">${m.avg_cost_usd.toFixed(6)}</td>
                  <td className="py-2 pr-3 text-right text-yellow-300 font-semibold">
                    {m.cost_per_correct_classification > 999
                      ? "∞"
                      : `$${m.cost_per_correct_classification.toFixed(5)}`}
                  </td>
                  <td className="py-2 pr-3 text-right text-gray-400">{m.p50_ms.toFixed(0)}</td>
                  <td className="py-2 pr-3 text-right text-gray-400">{m.p95_ms.toFixed(0)}</td>
                  <td className="py-2 text-right text-gray-400">{(m.error_rate * 100).toFixed(1)}%</td>
                  <td className="py-2 pl-3 text-sm">{roleLabel(m.model)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Cost per correct chart */}
      <div className="mb-6">
        <h3 className="text-sm font-semibold text-gray-300 mb-3">
          Cost per correct classification (lower = better)
        </h3>
        <ResponsiveContainer width="100%" height={180}>
          <BarChart data={costChartData} margin={{ top: 5, right: 20, left: 20, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis dataKey="model" tick={{ fontSize: 10, fill: "#9ca3af" }} />
            <YAxis tick={{ fontSize: 10, fill: "#9ca3af" }} />
            <Tooltip
              contentStyle={{ background: "#1f2937", border: "none", borderRadius: 8 }}
              formatter={(v: number) => `$${v.toFixed(6)}`}
            />
            <Bar dataKey="cost_per_correct" name="Cost/Correct" radius={[3, 3, 0, 0]}>
              {costChartData.map((entry, idx) => (
                <Cell
                  key={idx}
                  fill={entry.isRecommended ? "#22c55e" : entry.isFrontier ? "#ef4444" : "#6366f1"}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
        {frontier && rec.model_a !== rec.frontier_baseline && (
          <p className="text-center text-sm text-green-400 mt-2">
            Switching from {rec.frontier_baseline} → {rec.model_a} saves{" "}
            <strong>{rec.cost_savings_vs_frontier_pct.toFixed(1)}%</strong> on cost per correct answer
          </p>
        )}
      </div>

      {/* Cost extrapolation table */}
      {frontier && (
        <div>
          <h3 className="text-sm font-semibold text-gray-300 mb-3">
            Monthly cost extrapolation
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left border-b border-gray-700">
                  <th className="py-2 pr-4 text-gray-400">Volume / month</th>
                  {summaries.slice(0, 4).map((m) => (
                    <th key={m.model} className="py-2 pr-4 text-gray-400 text-right">
                      {m.model.split(/[-_]/)[0]}
                    </th>
                  ))}
                  {summaries.length > 0 && (
                    <th className="py-2 text-green-400 text-right">Savings (rec vs frontier)</th>
                  )}
                </tr>
              </thead>
              <tbody>
                {MONTHLY_VOLUMES.map((vol) => (
                  <tr key={vol} className="border-b border-gray-800">
                    <td className="py-2 pr-4 text-gray-300">
                      {vol.toLocaleString()}
                    </td>
                    {summaries.slice(0, 4).map((m) => (
                      <td
                        key={m.model}
                        className={`py-2 pr-4 text-right ${
                          m.model === rec.model_a ? "text-green-300 font-semibold" :
                          m.model === rec.frontier_baseline ? "text-red-300" : "text-gray-400"
                        }`}
                      >
                        ${monthlyCost(m.avg_cost_usd, vol)}
                      </td>
                    ))}
                    {summaries.length > 0 && (() => {
                      const recModel = summaries.find((s) => s.model === rec.model_a);
                      if (!recModel || !frontier) return <td />;
                      const savingsDollars = (frontier.avg_cost_usd - recModel.avg_cost_usd) * vol;
                      const savingsPct = frontier.avg_cost_usd > 0
                        ? ((frontier.avg_cost_usd - recModel.avg_cost_usd) / frontier.avg_cost_usd * 100).toFixed(0)
                        : "0";
                      return (
                        <td className="py-2 text-right text-green-400 font-semibold">
                          ${savingsDollars.toFixed(0)} ({savingsPct}%)
                        </td>
                      );
                    })()}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-xs text-gray-600 mt-2">
              * Based on avg tokens/issue × per-token rates from config.yaml pricing table.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
