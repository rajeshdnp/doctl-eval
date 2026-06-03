/**
 * Unscored view — agreement rate, distribution charts, disagreement table.
 * Shows model behavior on unlabeled issues.
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
} from "recharts";
import { MetricsPanel } from "./MetricsPanel";
import type { RunResults } from "../api/client";

const LABELS = ["bug", "enhancement", "question", "documentation", "security", "other"];

interface Props {
  results: RunResults;
}

export function UnscoredView({ results }: Props) {
  const [showOnlyDisagreements, setShowOnlyDisagreements] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const { unscored, manifest } = results;

  const distChartData = LABELS.map((label) => ({
    label,
    "Model A": unscored.per_class_distributions.model_a[label] ?? 0,
    "Model B": unscored.per_class_distributions.model_b[label] ?? 0,
  }));

  return (
    <div>
      {/* Headline stats */}
      <div className="grid grid-cols-2 gap-4 mb-6">
        <div className="bg-gray-800 rounded-xl p-5 text-center">
          <div className="text-3xl font-bold text-white mb-1">
            {(unscored.agreement_rate * 100).toFixed(1)}%
          </div>
          <div className="text-sm text-gray-400">Agreement Rate</div>
          <div className="text-xs text-gray-500 mt-1">
            Models agree on this % of unscored issues
          </div>
        </div>
        <div className="bg-gray-800 rounded-xl p-5 text-center">
          <div className="text-3xl font-bold text-white mb-1">
            {unscored.kappa.toFixed(3)}
          </div>
          <div className="text-sm text-gray-400">Cohen's κ</div>
          <div className="text-xs text-gray-500 mt-1">
            Agreement corrected for chance — report with agreement rate, not standalone
          </div>
        </div>
      </div>

      {/* Distribution charts */}
      <div className="mb-6">
        <h3 className="text-sm font-semibold text-gray-300 mb-3">
          Per-class prediction distribution (unscored issues)
        </h3>
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={distChartData} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis dataKey="label" tick={{ fontSize: 11, fill: "#9ca3af" }} />
            <YAxis tick={{ fontSize: 11, fill: "#9ca3af" }} />
            <Tooltip
              contentStyle={{ background: "#1f2937", border: "none", borderRadius: 8 }}
            />
            <Legend />
            <Bar dataKey="Model A" fill="#6366f1" radius={[2, 2, 0, 0]} />
            <Bar dataKey="Model B" fill="#f97316" radius={[2, 2, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Disagreement table */}
      <div>
        <div className="flex items-center gap-3 mb-3">
          <h3 className="text-sm font-semibold text-gray-300">
            Unscored Issues ({unscored.disagreements.length} disagreements)
          </h3>
          <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer">
            <input
              type="checkbox"
              checked={showOnlyDisagreements}
              onChange={(e) => setShowOnlyDisagreements(e.target.checked)}
              className="rounded"
            />
            Show only disagreements
          </label>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left border-b border-gray-700">
                <th className="py-2 pr-3 text-gray-400 font-medium">Issue</th>
                <th className="py-2 pr-3 text-blue-400 font-medium">
                  Model A ({manifest.model_a.split(/[-_]/)[0]})
                </th>
                <th className="py-2 pr-3 text-orange-400 font-medium">
                  Model B ({manifest.model_b.split(/[-_]/)[0]})
                </th>
              </tr>
            </thead>
            <tbody>
              {unscored.disagreements.slice(0, 100).map((d) => (
                <>
                  <tr
                    key={d.issue_id}
                    className="border-b border-gray-800 hover:bg-gray-800/50 cursor-pointer"
                    onClick={() => setExpandedId(expandedId === d.issue_id ? null : d.issue_id)}
                  >
                    <td className="py-2 pr-3">
                      <span className="text-gray-500 text-xs">#{d.issue_number}</span>{" "}
                      <span className="text-gray-200">{d.title?.slice(0, 60)}</span>
                    </td>
                    <td className="py-2 pr-3">
                      <span className="px-2 py-0.5 rounded-full text-xs bg-blue-900 text-blue-300">
                        {d.model_a_label ?? "—"}
                      </span>
                    </td>
                    <td className="py-2">
                      <span className="px-2 py-0.5 rounded-full text-xs bg-orange-900 text-orange-300">
                        {d.model_b_label ?? "—"}
                      </span>
                    </td>
                  </tr>
                  {expandedId === d.issue_id && (
                    <tr key={`${d.issue_id}-exp`} className="bg-gray-900">
                      <td colSpan={3} className="py-2 px-4 text-xs text-gray-400">
                        <em>Click "View Raw" in the scored view to see full model reasoning for this issue.</em>
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <MetricsPanel results={results} />
    </div>
  );
}
