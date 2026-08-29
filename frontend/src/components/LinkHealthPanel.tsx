import { useCallback, useEffect, useRef, useState } from 'react';
import type { EvaluationResult, LinkState, WhatIfEvalResponse } from '../types/domain';
import type { HistoricalSnrPoint } from '../types/experience';
import { whatIfEvaluate } from '../api/client';
import { presentationLinkStatus, presentationSnrTrend } from '../experience/linkPresentation';

// ── Inner SVG paths helper (renders inside our own <svg>) ─────────────────────
const POINTS = 90;

function SignalWaveformPaths({ snrDb, width, height }: { snrDb: number; width: number; height: number }) {
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
    <>
      <line x1={0} y1={midY} x2={width} y2={midY} stroke="var(--border)" strokeWidth={1} strokeDasharray="2 5" />
      <path d={d.trim()} fill="none" stroke={stroke} strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round" />
    </>
  );
}

interface Props {
  linkState: LinkState;
  /** Optional historical SNR data from experience manifest for ASTERIA scenarios. */
  snrHistory?: HistoricalSnrPoint[];
  /** Called when what-if evals arrive so PlanSwitcher can reflect them. */
  onWhatIfResult?: (result: WhatIfEvalResponse, snrDb: number) => void;
}

const SNR_MIN = -15;
const SNR_MAX = 25;
const DEBOUNCE_MS = 350;

export function LinkHealthPanel({ linkState: ls, snrHistory, onWhatIfResult }: Props) {
  // Feature 5: what-if SNR slider state
  const [sliderSnr, setSliderSnr] = useState<number>(ls.snr_db);
  const [isPreview, setIsPreview] = useState(false);
  const [whatIfLoading, setWhatIfLoading] = useState(false);
  const [whatIfError, setWhatIfError] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // When the upstream linkState changes (refresh), reset slider
  useEffect(() => {
    setSliderSnr(ls.snr_db);
    setIsPreview(false);
  }, [ls.snr_db]);

  const runWhatIf = useCallback(async (snrDb: number) => {
    if (snrDb === ls.snr_db) {
      setIsPreview(false);
      return;
    }
    setWhatIfLoading(true);
    setWhatIfError(null);
    try {
      const result = await whatIfEvaluate(snrDb);
      onWhatIfResult?.(result, snrDb);
      setIsPreview(true);
    } catch (err) {
      setWhatIfError(String(err));
    } finally {
      setWhatIfLoading(false);
    }
  }, [ls.snr_db, onWhatIfResult]);

  function handleSliderChange(e: React.ChangeEvent<HTMLInputElement>) {
    const val = parseFloat(e.target.value);
    setSliderSnr(val);
    if (debounceRef.current !== null) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => runWhatIf(val), DEBOUNCE_MS);
  }

  function handleReset() {
    setSliderSnr(ls.snr_db);
    setIsPreview(false);
    const emptyContext = {
      base_snr_db: null,
      base_ber: null,
      requested_snr_db: null,
      requested_ber: null,
      effective_snr_db: null,
      effective_eb_n0_db: null,
      derived_ber_before_override: null,
      effective_ber: 0,
      snr_override_applied: false,
      ber_override_applied: false,
    };
    onWhatIfResult?.(
      {
        what_if_context: emptyContext,
        hypothetical_link_state: ls,
        evaluations: [],
        risk_weights: { w_deadline_miss: 0, w_critical_deficit: 0, w_window_pressure: 0 },
      },
      ls.snr_db,
    );
  }

  // Compact link status summary — use production presentation helpers
  const snr = ls.snr_db;
  const stability = (ls.link_stability * 100).toFixed(0);
  // Use historical SNR for trend if available (ASTERIA)
  const snrTrend = presentationSnrTrend(snr, snrHistory);
  // Use production link status (does not let low BER mask degraded SNR)
  const presStatus = presentationLinkStatus(ls);
  const linkStatus = presStatus;
  const linkStatusColor =
    linkStatus === 'CRITICAL' ? 'var(--critical)' : linkStatus === 'DEGRADED' ? 'var(--warn)' : 'var(--signal)';

  return (
    <section className="panel">
<h2>Link Health</h2>

{/* Compact summary badges */}
<div style={{
  display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 6, marginBottom: 8,
}}>
  {[
    { label: 'CURRENT SNR', value: `${ls.snr_db.toFixed(1)} dB` },
    { label: 'TREND', value: snrTrend },
    { label: 'STABILITY', value: `${stability}%` },
    { label: 'LINK STATE', value: linkStatus, color: linkStatusColor },
  ].map(({ label, value, color }) => (
    <div key={label} style={{
      background: '#f5f6f8', border: '1px solid #dde1e8',
      borderRadius: 3, padding: '4px 6px',
    }}>
      <div style={{ color: 'var(--text-muted)', fontSize: 9, fontFamily: 'var(--font-mono)', marginBottom: 2 }}>{label}</div>
      <div style={{ color: color ?? 'var(--text)', fontSize: 11, fontFamily: 'var(--font-mono)', fontWeight: 600 }}>{value}</div>
    </div>
  ))}
</div>

{/* Full-width responsive waveform */}
<div className="waveform-wrap" style={{ width: '100%', marginBottom: 6 }}>
  <svg
    width="100%"
    height={60}
    viewBox={`0 0 340 60`}
    preserveAspectRatio="none"
    className="signal-waveform"
    role="img"
    aria-label={`Signal waveform, SNR ${sliderSnr.toFixed(1)} dB`}
    style={{ display: 'block' }}
  >
    <SignalWaveformPaths snrDb={sliderSnr} width={340} height={60} />
  </svg>
</div>
<table>
<tbody>
<tr>
<td>SNR</td>
<td>
            {ls.snr_db.toFixed(1)} dB
            {isPreview && (
              <span style={{
                marginLeft: 8, color: 'var(--warn)',
                fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 600,
              }}>
                → {sliderSnr.toFixed(1)} dB
              </span>
            )}
          </td>
</tr>
<tr>
<td>Eb/N₀</td>
<td>{ls.eb_n0_db.toFixed(1)} dB</td>
</tr>
<tr>
<td>BER</td>
<td>{ls.ber.toExponential(2)}</td>
</tr>
<tr>
<td>RSSI</td>
<td>{ls.rssi_dbm.toFixed(1)} dBm</td>
</tr>
<tr>
<td>Goodput</td>
<td>{(ls.link_goodput_bps / 1000).toFixed(1)} kbps</td>
</tr>
<tr>
<td>Stability</td>
<td>{(ls.link_stability * 100).toFixed(0)}%</td>
</tr>
<tr>
<td>Window remaining</td>
<td>{ls.remaining_window_s.toFixed(1)} s</td>
</tr>
<tr>
  <td title="Link-layer / protocol-stack latency (headers, ACKs, framing). NOT signal propagation delay — see Comm Geometry panel for spacecraft signal travel time.">
    Protocol Latency
  </td>
  <td>{ls.latency_s.toFixed(3)} s</td>
</tr>
</tbody>
</table>

      {/* Feature 5: What-if SNR slider */}
      <div className="whatif-section">
        <div className="whatif-header">
          <span className="whatif-label">What-if SNR</span>
          {isPreview && (
            <span className="whatif-preview-badge">PREVIEW</span>
          )}
          {whatIfLoading && (
            <span style={{ color: 'var(--text-muted)', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
              evaluating…
            </span>
          )}
          {whatIfError && (
            <span style={{ color: 'var(--critical)', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
              error
            </span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <input
            type="range"
            min={SNR_MIN}
            max={SNR_MAX}
            step={0.5}
            value={sliderSnr}
            onChange={handleSliderChange}
            className="whatif-slider"
            aria-label={`What-if SNR: ${sliderSnr.toFixed(1)} dB`}
          />
          <span style={{
            color: isPreview ? 'var(--warn)' : 'var(--text)',
            fontSize: 12, fontFamily: 'var(--font-mono)', fontWeight: 600, minWidth: 60,
          }}>
            {sliderSnr.toFixed(1)} dB
          </span>
          {isPreview && (
            <button className="whatif-reset" onClick={handleReset} title="Reset to actual SNR">
              ✕
            </button>
          )}
        </div>
        {isPreview && (
          <p style={{ color: 'var(--warn)', fontSize: 11, fontFamily: 'var(--font-mono)', marginTop: 4, marginBottom: 0 }}>
            ⚠ Plan comparison shows SIMULATED conditions — not actual link state
          </p>
        )}
      </div>
    </section>
  );
}

/** Returned by the what-if slider when it has results — used for preview display in PlanSwitcher. */
export type WhatIfEval = EvaluationResult;
