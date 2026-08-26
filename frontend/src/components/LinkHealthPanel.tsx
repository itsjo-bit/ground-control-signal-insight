import { useCallback, useEffect, useRef, useState } from 'react';
import type { EvaluationResult, LinkState, WhatIfEvalResponse } from '../types/domain';
import { whatIfEvaluate } from '../api/client';
import { SignalWaveform } from './SignalWaveform';

interface Props {
  linkState: LinkState;
  /** Called when what-if evals arrive so PlanSwitcher can reflect them. */
  onWhatIfResult?: (result: WhatIfEvalResponse, snrDb: number) => void;
}

const SNR_MIN = -15;
const SNR_MAX = 25;
const DEBOUNCE_MS = 350;

export function LinkHealthPanel({ linkState: ls, onWhatIfResult }: Props) {
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

  return (
    <section className="panel">
<h2>Link Health</h2>
<div className="waveform-wrap">
<SignalWaveform snrDb={sliderSnr} width={340} height={60} />
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
  <td title="Link-layer / protocol-stack latency (headers, ACKs, framing). NOT free-space propagation delay — see Comm Geometry panel for spacecraft signal travel time.">
    Link Latency
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
