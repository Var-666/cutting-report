export function throwIfAborted(signal?: AbortSignal | null) {
  if (signal?.aborted) throw new DOMException('请求已取消', 'AbortError')
}
