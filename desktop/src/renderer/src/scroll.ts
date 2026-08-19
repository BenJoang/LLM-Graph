export const FOLLOW_BOTTOM_THRESHOLD = 72

export interface ScrollMetrics {
  scrollHeight: number
  scrollTop: number
  clientHeight: number
}

export function isNearBottom(
  metrics: ScrollMetrics,
  threshold = FOLLOW_BOTTOM_THRESHOLD,
) {
  return metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight <= threshold
}
