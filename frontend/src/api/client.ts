/**
 * API client — typed wrappers for all backend routes.
 * All functions are async and throw on non-2xx responses.
 */

const BASE = "";

export interface ModelInfo {
  slug: string;
  display_name: string;
  input_price_per_1m: number;
  output_price_per_1m: number;
  role: "frontier_baseline" | "open_source" | "budget" | "mid_range";
}

export interface HealthStatus {
  status: string;
  corpus_loaded: boolean;
  sweep_available: boolean;
  corpus_size: number;
  scored_size: number;
}

export interface CorpusInfo {
  total: number;
  scored: number;
  unscored: number;
  coverage_pct: number;
  fingerprint: string;
  class_distribution: Record<string, number>;
  caveats: string[];
  low_support_classes: string[];
}

export interface ModelSummary {
  model: string;
  accuracy: number;
  macro_f1: number;
  weighted_f1: number;
  avg_cost_usd: number;
  total_cost_usd: number;
  cost_per_correct_classification: number;
  p50_ms: number;
  p95_ms: number;
  throughput_rps: number;
  error_rate: number;
}

export interface SweepRecommendation {
  model_a: string;
  model_b: string;
  frontier_baseline: string;
  cost_savings_vs_frontier_pct: number;
  accuracy_delta_vs_frontier_pct: number;
  rationale: string;
}

export interface SweepResults {
  metadata: {
    sweep_date: string;
    models_evaluated: string[];
    dataset_fingerprint: string;
    total_issues: number;
    scored_issues: number;
  };
  model_summaries: ModelSummary[];
  recommendation: SweepRecommendation;
}

export interface PerClassMetrics {
  precision: number;
  recall: number;
  f1: number;
  support: number;
  f1_ci_lower: number;
  f1_ci_upper: number;
}

export interface RunMetrics {
  model: string;
  n_scored: number;
  n_correct: number;
  overall_accuracy: number;
  accuracy_ci: { lower: number; upper: number; method: string };
  per_class: Record<string, PerClassMetrics>;
  macro_f1: number;
  weighted_f1: number;
  confusion_matrix_raw: Record<string, Record<string, number>>;
  confusion_matrix_normalized: Record<string, Record<string, number>>;
  total_cost_usd: number;
  cost_per_correct_classification: number;
  security_warning: string | null;
}

export interface Disagreement {
  issue_id: number;
  issue_number: number | null;
  title: string | null;
  model_a_label: string | null;
  model_b_label: string | null;
  ground_truth_label: string | null;
  model_a_reasoning: string | null;
  model_b_reasoning: string | null;
}

export interface RunResults {
  run_id: string;
  manifest: {
    run_id: string;
    model_a: string;
    model_b: string;
    timestamp: string;
    concurrency: number;
    total_issues: number;
    scored_issues: number;
  };
  scored: {
    model_a: RunMetrics;
    model_b: RunMetrics;
    comparison: {
      agreement_rate: number;
      cohens_kappa: number;
      mcnemar: {
        chi2: number;
        p_value: number;
        is_significant: boolean;
        verdict: string;
      };
      disagreements: Disagreement[];
    };
  };
  unscored: {
    agreement_rate: number;
    kappa: number;
    per_class_distributions: {
      model_a: Record<string, number>;
      model_b: Record<string, number>;
    };
    disagreements: Array<{
      issue_id: number;
      issue_number: number | null;
      title: string | null;
      model_a_label: string | null;
      model_b_label: string | null;
    }>;
  };
  operational: {
    model_a: OperationalMetrics;
    model_b: OperationalMetrics;
  };
}

export interface OperationalMetrics {
  p50_latency_ms: number;
  p95_latency_ms: number;
  wall_clock_seconds: number;
  throughput_rps: number;
  total_cost_usd: number;
  avg_cost_per_call_usd: number;
  concurrency: number;
  error_breakdown: Record<string, number>;
  error_rate: number;
  cache_hit_rate: number;
  cost_per_correct_classification: number;
}

async function apiFetch<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, opts);
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${path} failed (${res.status}): ${text}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => apiFetch<HealthStatus>("/api/health"),
  models: () => apiFetch<ModelInfo[]>("/api/models"),
  corpus: () => apiFetch<CorpusInfo>("/api/corpus"),
  sweep: () => apiFetch<SweepResults>("/api/sweep"),
  results: (runId: string) => apiFetch<RunResults>(`/api/results/${runId}`),
};

export type SSEEvent =
  | { type: "progress"; model: string; completed: number; total: number; current_cost: number }
  | { type: "done"; run_id: string; total_cost: number }
  | { type: "error"; message: string };

/**
 * Run evaluation via SSE stream.
 * Calls onEvent for each SSE event. Returns the run_id on success.
 */
export async function startRun(
  modelA: string,
  modelB: string,
  concurrency: number | null,
  onEvent: (event: SSEEvent) => void
): Promise<string> {
  const res = await fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_a: modelA, model_b: modelB, concurrency }),
  });

  if (!res.ok) {
    throw new Error(`Run failed: ${res.statusText}`);
  }

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let runId = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    const chunk = decoder.decode(value, { stream: true });
    const lines = chunk.split("\n");

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          const event = JSON.parse(line.slice(6)) as SSEEvent;
          onEvent(event);
          if (event.type === "done") {
            runId = event.run_id;
          }
        } catch {
          // Skip malformed events
        }
      }
    }
  }

  return runId;
}
