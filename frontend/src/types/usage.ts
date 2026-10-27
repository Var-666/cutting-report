export interface UsageMetrics {
  call_count: number;
  metered_count: number;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  failed_count: number;
  input_metered_count: number;
  output_metered_count: number;
  average_duration_ms: number | null;
  cached_tokens: number | null;
  reasoning_tokens: number | null;
}
export interface UsageReport {
  start_date: string;
  end_date: string;
  scene: string;
  breakdown: (UsageMetrics & {
    scene: string;
    model: string;
  })[];
  granularity: string;
  timezone: string;
  summary: UsageMetrics;
  series: ({
    [K in keyof UsageMetrics]: UsageMetrics[K] | null;
  } & {
    period: string;
    before_tracking: boolean;
  })[];
}
