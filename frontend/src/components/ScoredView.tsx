/**
 * Scored view — accuracy, per-class F1, confusion matrices, disagreement table.
 * Only issues with ground-truth labels are shown here.
 */
import { useState } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  ErrorBar,
} from "recharts";
import { ConfusionMatrix } from "./ConfusionMatrix";
import { MetricsPanel } from "./MetricsPanel";
import type { RunResults, Disagreement } from "../api/client";

const LABELS = ["bug", "enhancement", "question", "documentation", "security", "other"];

interface Props {
  results: RunResults;
}

function KPICard({
  label,
  value,
  sub,
  highlight,
}: {
  label: string;
  value: string;
  sub?: string;
  highlight?: boolean;
}) {
  return (
    <div
      className={`rounded-xl p-4 ${
        highlight ? "bg-indigo-950 border border-indigo-500" : "bg-gray-800"
      }`}
    >
      <div className="text-2xl font-bold text-white">{value}</div>
      <div className="text-sm text-gray-400">{label}</div>
      {sub && <div className="text-xs text-gray-500 mt-1">{sub}</div>}
    </div>
  );
}

function DisagreementTable({
  disagreements,
  filter,
}: {
  disagreements: Disagreement[];
  filter: { true_label: string; pred_label: string } | null;
}) {
  const [expanded, setExpanded] = useState<number | null>(null);
  const [labelFilter, setLabelFilter] = useState<string>("all");

  let filtered = disagreements;
  if (filter) {
    filtered = filtered.filter(
      (d) => d.ground_truth_label === filter.true_label && d.model_a_label === filter.pred_label
    );
  }
  if (labelFilter !== "all") {
    filtered = filtered.filter(
      (d) => d.ground_truth_label === labelFilter || d.model_a_label === labelFilter || d.model_b_label === labelFilter
    );
  }

  return (
    <div className="mt-4">
      <div className="flex items-center gap-3 mb-3">
        <h3 className="text-sm font-semibold text-gray-300">
          Disagreements ({filtered.length}
          {filter ? ` — filtered by (${filter.true_label} → ${filter.pred_label})` : ""})
        </h3>
        <select
          className="text-xs bg-gray-800 border border-gray-600 rounded px-2 py-1 text-gray-300"
          value={labelFilter}
          onChange={(e) => setLabelFilter(e.target.value)}
        >
          <option value="all">All labels</option>
          {LABELS.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
        {filter && (
          <button
            className="text-xs text-indigo-400 hover:text-indigo-300"
            onClick={() => setExpanded(null)}
          >
            Clear matrix filter
          </button>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left border-b border-gray-700">
              <th className="py-2 pr-3 text-gray-400 font-medium">Issue</th>
              <th className="py-2 pr-3 text-blue-400 font-medium">Model A</th>
              <th className="py-2 pr-3 text-orange-400 font-medium">Model B</th>
              <th className="py-2 pr-3 text-green-400 font-medium">Ground Truth</th>
              <th className="py-2 text-gray-400 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 100).map((d) => (
              <>
                <tr
                  key={d.issue_id}
                  className="border-b border-gray-800 hover:bg-gray-800/50 cursor-pointer"
                  onClick={() => setExpanded(expanded === d.issue_id ? null : d.issue_id)}
                >
                  <td className="py-2 pr-3">
                    <span className="text-gray-500 text-xs">#{d.issue_number}</span>{" "}
                    <span className="text-gray-200 truncate max-w-xs inline-block">
                      {d.title?.slice(0, 60)}
                    </span>
                  </td>
                  <td className="py-2 pr-3">
                    <span className="px-2 py-0.5 rounded-full text-xs bg-blue-900 text-blue-300">
                      {d.model_a_label ?? "—"}
                    </span>
                  </td>
                  <td className="py-2 pr-3">
                    <span className="px-2 py-0.5 rounded-full text-xs bg-orange-900 text-orange-300">
                      {d.model_b_label ?? "—"}
                    </span>
                  </td>
                  <td className="py-2 pr-3">
                    {d.ground_truth_label ? (
                      <span className="px-2 py-0.5 rounded-full text-xs bg-green-900 text-green-300">
                        {d.ground_truth_label}
                      </span>
                    ) : (
                      <span className="text-gray-600 text-xs">no GT</span>
                    )}
                  </td>
                  <td className="py-2">
                    <span className="text-xs text-gray-500">
                      {expanded === d.issue_id ? "▲ hide" : "▼ raw"}
                    </span>
                  </td>
                </tr>
                {expanded === d.issue_id && (
                  <tr key={`${d.issue_id}-expanded`} className="bg-gray-900">
                    <td colSpan={5} className="py-3 px-4">
                      <div className="grid grid-cols-2 gap-4">
                        <div>
                          <div className="text-xs font-semibold text-blue-400 mb-1">
                            Model A reasoning
                          </div>
                          <div className="text-xs text-gray-400 whitespace-pre-wrap">
                            {d.model_a_reasoning ?? "(none)"}
                          </div>
                        </div>
                        <div>
                          <div className="text-xs font-semibold text-orange-400 mb-1">
                            Model B reasoning
                          </div>
                          <div className="text-xs text-gray-400 whitespace-pre-wrap">
                            {d.model_b_reasoning ?? "(none)"}
                          </div>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
        {filtered.length > 100 && (
          <p className="text-xs text-gray-500 mt-2">Showing first 100 of {filtered.length}</p>
        )}
      </div>
    </div>
  );
}

export function ScoredView({ results }: Props) {
  const [matrixFilter, setMatrixFilter] = useState<{
    true_label: string;
    pred_label: string;
  } | null>(null);

  const { scored, manifest } = results;
  const { model_a: mA, model_b: mB, comparison } = scored;

  // Per-class F1 chart data
  const f1ChartData = LABELS.map((label) => ({
    label,
    [`Model A (${manifest.model_a.split(/[-_]/)[0]})`]: mA.per_class[label]?.f1 ?? 0,
    [`Model B (${manifest.model_b.split(/[-_]/)[0]})`]: mB.per_class[label]?.f1 ?? 0,
    // Error bar ranges for model A
    errorA: [
      (mA.per_class[label]?.f1 ?? 0) - (mA.per_class[label]?.f1_ci_lower ?? 0),
      (mA.per_class[label]?.f1_ci_upper ?? 0) - (mA.per_class[label]?.f1 ?? 0),
    ],
  }));

  const ciText = (acc: number, ci: { lower: number; upper: number }) =>
    `${(acc * 100).toFixed(1)}% [${(ci.lower * 100).toFixed(1)}–${(ci.upper * 100).toFixed(1)}%]`;

  return (
    <div>
      {/* KPI row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <KPICard
          label={`Model A accuracy`}
          value={ciText(mA.overall_accuracy, mA.accuracy_ci)}
          sub={manifest.model_a}
        />
        <KPICard
          label={`Model B accuracy`}
          value={ciText(mB.overall_accuracy, mB.accuracy_ci)}
          sub={manifest.model_b}
        />
        <KPICard
          label="Cost/Correct — Model A ⭐"
          value={`$${mA.cost_per_correct_classification.toFixed(5)}`}
          highlight
          sub="key business metric"
        />
        <KPICard
          label="Cost/Correct — Model B ⭐"
          value={`$${mB.cost_per_correct_classification.toFixed(5)}`}
          sub="key business metric"
        />
      </div>

      {/* McNemar result */}
      <div
        className={`rounded-lg p-3 mb-6 text-sm ${
          comparison.mcnemar.is_significant
            ? "bg-indigo-950 border border-indigo-600"
            : "bg-gray-800"
        }`}
      >
        <span className="font-semibold text-gray-300">McNemar's test: </span>
        <span className="text-gray-200">{comparison.mcnemar.verdict}</span>
        <span className="text-gray-500 text-xs ml-2">
          (χ²={comparison.mcnemar.chi2.toFixed(2)}, agreement={" "}
          {(comparison.agreement_rate * 100).toFixed(1)}%, κ=
          {comparison.cohens_kappa.toFixed(3)})
        </span>
        {mA.security_warning && (
          <div className="mt-2 text-yellow-400 text-xs">⚠ {mA.security_warning}</div>
        )}
      </div>

      {/* Per-class F1 chart */}
      <div className="mb-6">
        <h3 className="text-sm font-semibold text-gray-300 mb-3">
          Per-class F1 Score (side-by-side)
        </h3>
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={f1ChartData} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis dataKey="label" tick={{ fontSize: 11, fill: "#9ca3af" }} />
            <YAxis domain={[0, 1]} tick={{ fontSize: 11, fill: "#9ca3af" }} />
            <Tooltip
              contentStyle={{ background: "#1f2937", border: "none", borderRadius: 8 }}
              formatter={(v: number) => v.toFixed(3)}
            />
            <Legend />
            <Bar
              dataKey={`Model A (${manifest.model_a.split(/[-_]/)[0]})`}
              fill="#6366f1"
              radius={[2, 2, 0, 0]}
            />
            <Bar
              dataKey={`Model B (${manifest.model_b.split(/[-_]/)[0]})`}
              fill="#f97316"
              radius={[2, 2, 0, 0]}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Confusion matrices */}
      <div className="flex flex-wrap gap-6 mb-6 justify-center">
        <ConfusionMatrix
          title={`Model A: ${manifest.model_a}`}
          normalized={mA.confusion_matrix_normalized}
          raw={mA.confusion_matrix_raw}
          onCellClick={(t, p) => setMatrixFilter({ true_label: t, pred_label: p })}
        />
        <ConfusionMatrix
          title={`Model B: ${manifest.model_b}`}
          normalized={mB.confusion_matrix_normalized}
          raw={mB.confusion_matrix_raw}
          onCellClick={(t, p) => setMatrixFilter({ true_label: t, pred_label: p })}
        />
      </div>

      {/* Disagreement table */}
      <DisagreementTable
        disagreements={comparison.disagreements}
        filter={matrixFilter}
      />

      {/* Operational metrics */}
      <MetricsPanel results={results} />
    </div>
  );
}
