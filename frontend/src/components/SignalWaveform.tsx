import { useEffect, useState } from 'react';

interface Props {
  snrDb: number;
  width?: number;
  height?: number;
}

const POINTS = 90;

/**
 * Live-animated waveform driven by the link's actual SNR.
 *
 * This is a purely VISUAL mapping — not a telecom-accurate signal
 * reconstruction. Good SNR renders as a clean, calm wave; poor SNR
 * renders as jagged, high-amplitude noise. The mapping thresholds
 * (-10dB..15dB) are chosen for visual legibility across the scenarios
 * this app ships with, not derived from a channel model.
 */
export function SignalWaveform({ snrDb, width = 320, height = 64 }: Props) {
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 110);
    return () => clearInterval(id);
  }, []);

  const quality = Math.max(0, Math.min(1, (snrDb + 10) / 25));
  const amplitude = height * 0.3;
  const noiseAmp = (1 - quality) * height * 0.42;
  const midY = height / 2;
  const phase = tick * 0.32;

  let d = '';
  for (let i = 0; i < POINTS; i++) {
    const t = i / (POINTS - 1);
    const x = t * width;
    const clean = Math.sin(t * Math.PI * 6 + phase) * amplitude * quality;
    const seed = Math.sin(i * 12.9898 + phase * 7.233) * 43758.5453;
    const noise = (seed - Math.floor(seed) - 0.5) * 2 * noiseAmp;
    const y = midY + clean + noise;
    d += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1) + ' ';
  }

  const stroke =
    quality > 0.55 ? 'var(--signal)' : quality > 0.25 ? 'var(--warn)' : 'var(--critical)';

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className="signal-waveform"
      role="img"
      aria-label={`Signal waveform, SNR ${snrDb.toFixed(1)} dB`}
    >
<line
        x1={0}
        y1={midY}
        x2={width}
        y2={midY}
        stroke="var(--border)"
        strokeWidth={1}
        strokeDasharray="2 5"
      />
<path
        d={d.trim()}
        fill="none"
        stroke={stroke}
        strokeWidth={1.6}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
</svg>
  );
}
