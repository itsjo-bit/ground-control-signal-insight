/** Data unit formatters for GCSI frontend display. */

/** Format size_bits (bits) as human-readable decimal bytes (KB/MB/GB) */
export function formatBitsAsDataVolume(bits: number): string {
  const bytes = bits / 8;
  if (bytes >= 1_000_000_000) return `${(bytes / 1_000_000_000).toFixed(2)} GB`;
  if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(1)} MB`;
  if (bytes >= 1_000) return `${(bytes / 1_000).toFixed(1)} KB`;
  return `${Math.round(bytes)} B`;
}

/**
 * Format a bit count as Mbit / Gbit (decimal, not bytes).
 * Used for transmission/capacity values where bits are the authoritative unit.
 */
export function formatBitsAsMbit(bits: number): string {
  if (bits >= 1_000_000_000) return `${(bits / 1_000_000_000).toFixed(2)} Gbit`;
  if (bits >= 1_000_000) return `${(bits / 1_000_000).toFixed(1)} Mbit`;
  if (bits >= 1_000) return `${(bits / 1_000).toFixed(1)} kbit`;
  return `${Math.round(bits)} bit`;
}

/** Format bits/s as human-readable */
export function formatBitRate(bps: number): string {
  if (bps >= 1_000_000) return `${(bps / 1_000_000).toFixed(2)} Mbps`;
  if (bps >= 1_000) return `${(bps / 1_000).toFixed(1)} kbps`;
  return `${Math.round(bps)} bps`;
}

/** Format seconds as MM:SS or HH:MM:SS */
export function formatDuration(s: number): string {
  const hours = Math.floor(s / 3600);
  const mins = Math.floor((s % 3600) / 60);
  const secs = Math.floor(s % 60);
  if (hours > 0)
    return `${String(hours).padStart(2, '0')}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

/** Format distance in km */
export function formatDistanceKm(km: number): string {
  if (km >= 1_000_000) return `${(km / 1_000_000).toFixed(2)}M km`;
  if (km >= 1_000) return `${(km / 1_000).toFixed(1)}k km`;
  return `${Math.round(km)} km`;
}
