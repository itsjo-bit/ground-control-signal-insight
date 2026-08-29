/**
 * MissionStatusSummary — upper-right persistent zone of the four-zone layout.
 *
 * Purpose: "What is happening, and why is downlink prioritization necessary?"
 *
 * Shows the mission bottleneck at a glance using existing runtime values.
 * Reuses queuedDataBits / availableCapacityBits / dataProductsCount / LinkState /
 * MissionState / existing formatters — no new domain semantics introduced.
 *
 * Queue pressure = queuedDataBits / availableCapacityBits — same relationship
 * already computed in AsteriaMissionHero (RightPanel.tsx).
 */
import type { LinkState, MissionState } from '../types/domain';
import { formatBitsAsMbit, formatDuration } from '../utils/formatters';
import { presentationLinkStatus } from '../experience/linkPresentation';

const MONO = '"IBM Plex Mono", ui-monospace, "SF Mono", monospace';
const SANS = '"IBM Plex Sans", system-ui, sans-serif';

interface Props {
  linkState: LinkState | null;
  missionState: MissionState | null;
  availableCapacityBits: number;
  queuedDataBits: number;
  dataProductsCount: number;
}

function StatRow({
  label,
  value,
  color,
  unit,
}: {
  label: string;
  value: string;
  color?: string;
  unit?: string;
}) {
  return (
    <div style={{
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'baseline',
      padding: '4px 0',
      borderBottom: '1px solid #21262d',
    }}>
      <span style={{
        fontFamily: SANS,
        fontSize: 10,
        color: '#8b949e',
        letterSpacing: '0.01em',
        flexShrink: 0,
        paddingRight: 8,
      }}>
        {label}
      </span>
      <span style={{
        fontFamily: MONO,
        fontSize: 12,
        fontWeight: 700,
        color: color ?? '#e6edf3',
        textAlign: 'right',
      }}>
        {value}
        {unit && (
          <span style={{ fontSize: 9, color: '#656d76', marginLeft: 3 }}>{unit}</span>
        )}
      </span>
    </div>
  );
}

export function MissionStatusSummary({
  linkState: ls,
  missionState: ms,
  availableCapacityBits,
  queuedDataBits,
  dataProductsCount,
}: Props) {
  // Queue pressure: same ratio as AsteriaMissionHero
  const queuePressure = availableCapacityBits > 0 && queuedDataBits > 0
    ? queuedDataBits / availableCapacityBits
    : null;

  const queueMbit = queuedDataBits > 0 ? formatBitsAsMbit(queuedDataBits) : null;
  const capacityMbit = availableCapacityBits > 0 ? formatBitsAsMbit(availableCapacityBits) : null;

  const windowLabel = ms && ms.comm_window_remaining_s > 0
    ? formatDuration(Math.round(ms.comm_window_remaining_s))
    : null;

  const riskLevel = ms?.risk_level ?? null;
  const riskColor =
    riskLevel === 'CRITICAL' ? '#f85149' :
    riskLevel === 'HIGH'     ? '#f85149' :
    riskLevel === 'MEDIUM'   ? '#d29922' :
    riskLevel === 'LOW'      ? '#3fb950' : '#8b949e';

  const snrLabel = ls ? `${ls.snr_db.toFixed(1)} dB` : null;
  const snrColor = !ls ? '#8b949e' :
    ls.snr_db < 5  ? '#f85149' :
    ls.snr_db < 10 ? '#d29922' : '#3fb950';

  const linkStatus = ls ? presentationLinkStatus(ls) : null;
  const linkColor =
    linkStatus === 'CRITICAL' ? '#f85149' :
    linkStatus === 'DEGRADED' ? '#d29922' : '#3fb950';

  const pressureColor = queuePressure === null ? '#8b949e' :
    queuePressure > 50  ? '#f85149' :
    queuePressure > 10  ? '#f85149' :
    queuePressure > 2   ? '#d29922' : '#3fb950';

  return (
    <div
      data-testid="mission-status-summary"
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        background: '#161b22',
        borderLeft: '1px solid #30363d',
        overflow: 'hidden',
        minWidth: 0,
      }}
    >
      {/* Zone header */}
      <div style={{
        padding: '7px 12px 6px',
        borderBottom: '1px solid #30363d',
        flexShrink: 0,
        background: '#161b22',
        display: 'flex',
        alignItems: 'center',
        gap: 6,
      }}>
        <span style={{ fontSize: 10, color: '#484f58' }}>◉</span>
        <span style={{
          fontFamily: SANS,
          fontSize: 10,
          fontWeight: 600,
          color: '#8b949e',
          textTransform: 'uppercase',
          letterSpacing: '0.07em',
        }}>
          Mission Status
        </span>
      </div>

      {/* Scrollable content */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        overflowX: 'hidden',
        padding: '10px 12px',
        minHeight: 0,
      }}>
        {/* Data product queue */}
        <div style={{ marginBottom: 12 }}>
          <div style={{
            fontFamily: SANS,
            fontSize: 9,
            color: '#656d76',
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
            marginBottom: 6,
            paddingBottom: 4,
            borderBottom: '1px solid #21262d',
          }}>
            Downlink Queue
          </div>

          {dataProductsCount > 0 ? (
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 4 }}>
              <span
                data-testid="status-product-count"
                style={{
                  fontFamily: MONO,
                  fontSize: 28,
                  fontWeight: 700,
                  color: '#e6edf3',
                  lineHeight: 1,
                }}
              >
                {dataProductsCount}
              </span>
              <span style={{ fontFamily: SANS, fontSize: 10, color: '#8b949e' }}>
                products
              </span>
            </div>
          ) : (
            <div style={{ fontFamily: MONO, fontSize: 12, color: '#656d76' }}>—</div>
          )}

          {queueMbit && (
            <StatRow
              label="Queued data"
              value={queueMbit}
              color="#d29922"
            />
          )}
          {capacityMbit && (
            <StatRow
              label="Contact capacity"
              value={capacityMbit}
              color="#8b949e"
            />
          )}
          {queuePressure !== null && (
            <StatRow
              label="Queue pressure"
              value={`~${queuePressure.toFixed(0)}×`}
              color={pressureColor}
            />
          )}
        </div>

        {/* Contact window */}
        {(windowLabel || riskLevel) && (
          <div style={{ marginBottom: 12 }}>
            <div style={{
              fontFamily: SANS,
              fontSize: 9,
              color: '#656d76',
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
              marginBottom: 6,
              paddingBottom: 4,
              borderBottom: '1px solid #21262d',
            }}>
              Contact Window
            </div>
            {windowLabel && (
              <StatRow
                label="Remaining window"
                value={windowLabel}
                color={ms && ms.comm_window_remaining_s < 60 ? '#f85149' : '#3fb950'}
              />
            )}
            {riskLevel && (
              <StatRow
                label="Mission risk"
                value={riskLevel}
                color={riskColor}
              />
            )}
          </div>
        )}

        {/* Link */}
        {ls && (
          <div style={{ marginBottom: 12 }}>
            <div style={{
              fontFamily: SANS,
              fontSize: 9,
              color: '#656d76',
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
              marginBottom: 6,
              paddingBottom: 4,
              borderBottom: '1px solid #21262d',
            }}>
              Link State
            </div>
            {linkStatus && (
              <StatRow
                label="Link"
                value={linkStatus}
                color={linkColor}
              />
            )}
            {snrLabel && (
              <StatRow
                label="SNR"
                value={snrLabel}
                color={snrColor}
              />
            )}
            <StatRow
              label="Stability"
              value={`${(ls.link_stability * 100).toFixed(0)}%`}
              color={ls.link_stability < 0.5 ? '#f85149' : ls.link_stability < 0.75 ? '#d29922' : '#3fb950'}
            />
          </div>
        )}

        {!ls && !ms && (
          <div style={{ fontFamily: SANS, fontSize: 11, color: '#656d76', padding: '8px 0' }}>
            Loading mission status…
          </div>
        )}
      </div>
    </div>
  );
}
