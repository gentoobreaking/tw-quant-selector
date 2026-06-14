const prefersReduced = () =>
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

export function animateNumber(
  el: HTMLElement,
  from: number,
  to: number,
  duration: number = 400,
  formatter?: (v: number) => string,
): () => void {
  if (prefersReduced() || Math.abs(to - from) / Math.max(Math.abs(from), 1) < 0.05 && Math.abs(to - from) < 2) {
    el.textContent = formatter ? formatter(to) : String(to);
    return () => {};
  }

  const start = performance.now();
  let canceled = false;

  function tick(now: number) {
    if (canceled) return;
    const elapsed = Math.min(now - start, duration);
    const t = elapsed / duration;
    const eased = 1 - Math.pow(1 - t, 3);
    const current = from + (to - from) * eased;
    el.textContent = formatter ? formatter(current) : String(Math.round(current));
    if (elapsed < duration) requestAnimationFrame(tick);
  }

  requestAnimationFrame(tick);
  return () => { canceled = true; };
}
