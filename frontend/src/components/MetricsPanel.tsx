/**
 * Operational metrics panel — shown below both Scored and Unscored views.
 * Always shows concurrency alongside latency per exercise requirement.
 */
import type { OperationalMetrics, RunResults } from "../api/client";

interface Props {
  results: RunResults;
}

function MetricCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-gray-800 rounded-lg p-3">
      <div className="text-lg font-bold text-white">{value}</div>
      <div className="text-xs text-gray-400">{label}</div>
      {sub && <div className="text-xs text-gray-500 mt-0.5">{sub}</div>}
    </div>
  );
}

function ModelOpsColumn({
  label,
  ops,
  color,
}: {
  label: string;
  ops: OperationalMetrics;
  color: "blue" | "orange";
}) {
  const borderColor = color === "blue" ? "border-blue-600" : "border-orange-600";
  const textColor = color === "blue" ? "text-blue-400" : "text-orange-400";
  const errorEntries = Object.entries(ops.error_breakdown).filter(([, v]) => v > 0);

  return (
    <div className={`border ${borderColor} rounded-xl p-4 flex-1`}>
      <h4 className={`font-semibold mb-3 ${textColor}`}>{label}</h4>
      <div className="grid grid-cols-2 gap-2">
        <MetricCard
          label="p50 Latency"
          value={`${ops.p50_latency_ms.toFixed(0)}ms`}
          sub={`@ concurrency ${ops.concurrency}`}
        />
        <MetricCard
          label="p95 Latency"
          value={`${ops.p95_latency_ms.toFixed(0)}ms`}
          sub={`@ concurrency ${ops.concurrency}`}
        />
        <MetricCard
          label="Throughput"
          value={`${ops.throughput_rps.toFixed(1)} req/s`}
          sub={`wall: ${ops.wall_clock_seconds.toFixed(0)}s`}
        />
        <MetricCard
          label="Cache Hit Rate"
          value={`${(ops.cache_hit_rate * 100).toFixed(0)}%`}
          sub="cached = $0 actual cost"
        />
        <MetricCard
          label="Total Cost"
          value={`$${ops.total_cost_usd.toFixed(5)}`}
          sub={`avg $${ops.avg_cost_per_call_usd.toFixed(6)}/call`}
        />
        <MetricCard
          label="Cost / Correct ⭐"
          value={
            ops.cost_per_correct_classification === Infinity ||
            ops.cost_per_correct_classification > 999
              ? "∞"
              : `$${ops.cost_per_correct_classification.toFixed(5)}`
          }
          sub="key business metric"
        />
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        <span
          className={`text-xs px-2 py-1 rounded-full ${
            ops.error_rate > 0.05
              ? "bg-red-900 text-red-300"
              : "bg-gray-700 text-gray-300"
          }`}
        >
          Error rate: {(ops.error_rate * 100).toFixed(1)}%
        </span>
        {errorEntries.map(([type, count]) => (
          <span key={type} className="text-xs px-2 py-1 rounded-full bg-yellow-900/50 text-yellow-300">
            {type}: {count}
          </span>
        ))}
      </div>
    </div>
  );
}

export function MetricsPanel({ results }: Props) {
  const { manifest, operational } = results;

  return (
    <div className="mt-6">
      <h3 className="text-base font-semibold text-gray-300 mb-3">
        Operational Metrics — Run {manifest.run_id} ({manifest.total_issues} issues, concurrency{" "}
        {manifest.concurrency})
      </h3>
      <div className="flex gap-4">
        <ModelOpsColumn
          label={manifest.model_a}
          ops={operational.model_a}
          color="blue"
        />
        <ModelOpsColumn
          label={manifest.model_b}
          ops={operational.model_b}
          color="orange"
        />
      </div>
    </div>
  );
}
