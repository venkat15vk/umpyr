/** Umpyr mark: a U whose two arms meet at an amber dot. */
export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-label="Umpyr" role="img">
      <path d="M14 10v26a18 18 0 0 0 36 0V10" fill="none" stroke="var(--accent)" strokeWidth="9" strokeLinecap="round" />
      <circle cx="32" cy="55" r="6" fill="var(--warn)" />
    </svg>
  );
}
