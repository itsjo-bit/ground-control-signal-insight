/**
 * RightPanel — GCSI V3.5 contextual main control panel (right side).
 *
 * V3.5 changes:
 * - Accepts workspaceMode: 'normal' | 'expanded' | 'focus'
 * - Panel width driven by workspaceMode (normal=manual, expanded=clamp(650,58vw,1100), focus=full)
 * - Workspace mode controls in panel header (⇔ Expand, ⛶ Focus, ↩ Normal)
 * - AI Copilot: tabbed workspace (Prioritization / Reasoning / Decision)
 * - Mission Log: tabbed workspace (Simulation / Narrative / Report)
 * - Data: responsive columns by workspace mode
 * - Focus mode: header shows "FOCUS MODE" indicator with EXIT button
 */
import { useState, useMemo, useCallback, useEffect } from 'react';
import type { NavSection } from './NavigationSidebar';
import type {
  AIRecommendation,
  AiLifecycle,
  AnomalyEvent,
  ApproveResponse,
  CandidatePlan,
  CandidatePrioritization,
  DataProduct,
  DecisionMode,
  EvaluationResult,
  LinkState,
  MissionState,
  RankedProduct,
  ScenarioInfo,
  WhatIfEvalResponse,
} from '../types/domain';
import type { ExperienceManifest } from '../types/experience';
import type { ManualAssessmentResult } from '../experience/missionExperienceReducer';
import type { ApprovalPhase } from './ApprovalBar';
import type { ViewSettings } from '../hooks/useViewSettings';
import type { WorkspaceMode } from '../MissionControl';
import { formatBitsAsDataVolume, formatDuration } from '../utils/formatters';
import { presentationLinkStatus, presentationSnrTrend } from '../experience/linkPresentation';
import { countUrgentProducts } from '../experience/urgentCandidates';

// Import existing panels (preserved as-is)
import { MissionStatePanel } from './MissionStatePanel';
import { CommBudgetBar } from './CommBudgetBar';
import { SignalGeometryBlock } from './SignalGeometryBlock';
import { LinkHealthPanel } from './LinkHealthPanel';
import { AIDecisionPanel } from './AIDecisionPanel';
import { MissionDecisionPanel } from './MissionDecisionPanel';
import { RecommendationPanel } from './RecommendationPanel';
import { ApprovalBar } from './ApprovalBar';
import { TransmissionSummaryPanel } from './TransmissionSummaryPanel';
import { TransmissionOutcomeBanner } from './TransmissionOutcomeBanner';
import { TransmissionQueuePanel } from './TransmissionQueuePanel';
import { SimulationPanel } from './SimulationPanel';
import { TransmissionNarrativePanel } from './TransmissionNarrativePanel';
import { MissionReportPanel } from './MissionReportPanel';
import { TransmissionSequencePanel } from './TransmissionSequencePanel';
import { GroundReceptionPanel } from './GroundReceptionPanel';
import { ManualVsAiPanel } from './ManualVsAiPanel';
import { SessionLogPanel } from './SessionLogPanel';
import { ResizableSection } from './ResizableSection';
import { ConfigPanel } from './ConfigPanel';
import type { SessionEvent } from '../experience/missionExperienceReducer';

// ── Shared style tokens (V3.3 gray + blue, unchanged) ─────────────────────────

const CARD: React.CSSProperties = {
  background: 'rgba(18,24,34,0.7)',
  border: '1px solid rgba(46,58,79,0.8)',
  borderRadius: 10,
  padding: '12px 14px',
  marginBottom: 8,
  minWidth: 0,
  overflowX: 'hidden',
};

const LABEL: React.CSSProperties = {
  fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
  fontSize: 9,
  color: 'rgba(147,160,180,0.60)',
  letterSpacing: '0.04em',
  marginBottom: 4,
};

const VALUE: React.CSSProperties = {
  fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
  fontSize: 14,
  fontWeight: 700,
  lineHeight: 1,
};

/** Wraps a table in a horizontally scrollable container */
function TableScroll({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ overflowX: 'auto', overflowY: 'visible', minWidth: 0 }}>
      <div style={{ minWidth: 'max-content' }}>{children}</div>
    </div>
  );
}

// ── CommonProps — all data passed from MissionControl ──────────────────────────

interface CommonProps {
  linkState: LinkState | null;
  missionState: MissionState | null;
  distanceKm: number | null;
  propagationDelayS: number | null;
  roundTripTimeS: number | null;
  availableCapacityBits: number;
  queuedDataBits: number;
  dataProductsCount: number;
  anomalies: AnomalyEvent[];
  queue: CandidatePlan | null;
  recommendation: AIRecommendation | null;
  aiProvider: string | null;
  aiRequestedProvider: string | null;
  aiActualProvider: string | null;
  aiPrioritization: CandidatePrioritization | null;
  aiCandidateCount: number | null;
  aiPrioritizationError: string | null;
  aiPrioritizationFallbackReason: string | null;
  aiRecommendationFallbackReason: string | null;
  allPlans: CandidatePlan[];
  allEvaluations: EvaluationResult[];
  activePlanId: string;
  approvalPhase: ApprovalPhase;
  approveResult: ApproveResponse | null;
  whatIfEvals: EvaluationResult[] | null;
  whatIfSnr: number | null;
  recPlan: CandidatePlan | null;
  recEval: EvaluationResult | null;
  activeEval: EvaluationResult | null;
  activePlan: CandidatePlan;
  riskWeights: { w_deadline_miss: number; w_critical_deficit: number; w_window_pressure: number };
  onApproved: (result: ApproveResponse) => void;
  onTransmitting: () => void;
  onApprovalError: () => void;
  onWhatIfResult: (result: WhatIfEvalResponse, snrDb: number) => void;
  onSelectPlan: (planId: string) => void;
  // ── V3.4 props ───────────────────────────────────────────────────────────────
  decisionMode: DecisionMode;
  onSelectDecisionMode: (mode: DecisionMode) => void;
  aiLifecycle: AiLifecycle;
  aiError: string | null;
  onRunAiAnalysis: () => void;
  rawDataProducts: DataProduct[];
  hasDataProducts: boolean;
  manualSelectedIds: Set<string>;
  manualOrder: string[];
  manualPlan: CandidatePlan | null;
  onToggleManualSelect: (productId: string) => void;
  onClearManualSelection: () => void;
  onManualReorder: (newOrder: string[]) => void;
  availableScenarios: ScenarioInfo[];
  activeScenarioPath: string | null;
  scenarioSwitching: boolean;
  onSwitchScenario: (filename: string) => void;
  // ── V3.5 props ───────────────────────────────────────────────────────────────
  workspaceMode: WorkspaceMode;
  onSetWorkspaceMode: (mode: WorkspaceMode) => void;
  // ── Phase 4.2F props ─────────────────────────────────────────────────────────
  experienceManifest: ExperienceManifest | null;
  experienceAvailable: boolean;
  manualAssessment: ManualAssessmentResult | null;
  manualAssessmentLoading: boolean;
  manualAssessmentError: string | null;
  manualAssessmentStale: boolean;
  onManualEvaluate: () => void;
  onManualTransmit: () => void;
  // ── Phase 4.2F4 props ─────────────────────────────────────────────────────────
  /** When true, TransmissionSequencePanel is active (choreography in progress). */
  choreographyActive: boolean;
  /** Plan queued for execution during choreography. */
  pendingExecutionPlan: CandidatePlan | null;
  /** Stable execution identifier — the single-shot approval guard. */
  executionId: string | null;
  /** Wall-clock ms when playback started (null if not yet started). */
  playbackStartedAtMs: number | null;
  /** Called to set playbackStartedAtMs in the coordinator (once per execution). */
  onSetPlaybackStarted: (ms: number) => void;
  /** Execute the actual backend approval call (single-shot by executionId). */
  onExecuteApproval: (executionId: string) => Promise<import('../types/domain').ApproveResponse>;
  /** Called when choreography sequence fully completes. */
  onChoreographyComplete: (result: import('../types/domain').ApproveResponse) => void;
  /** Called when choreography encounters an error. */
  onChoreographyError: (msg: string) => void;
  // ── Phase 4.2F3 props ─────────────────────────────────────────────────────────
  /** Called when operator approves AI recommendation — does NOT execute transmission. */
  onApproveAiPlan: () => void;
  /** Called when operator wants to modify AI plan — seeds manual mode with AI plan IDs. */
  onModifyAiPlan: () => void;
  /** Called when operator rejects AI recommendation — no backend mutation. */
  onRejectAiPlan: () => void;
  /** Whether the AI recommendation was rejected this session. */
  aiRecommendationRejected: boolean;
  /** Session event log from missionExperienceReducer. */
  sessionEvents: SessionEvent[];
}

// ── StatGrid ──────────────────────────────────────────────────────────────────

function StatGrid({ items }: { items: { label: string; value: string; color: string }[] }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 10 }}>
      {items.map(({ label, value, color }) => (
        <div key={label} style={{
          background: 'rgba(18,24,34,0.7)',
          border: '1px solid rgba(46,58,79,0.8)',
          borderRadius: 8,
          padding: '8px 10px',
        }}>
          <div style={LABEL}>{label}</div>
          <div style={{ ...VALUE, color }}>{value}</div>
        </div>
      ))}
    </div>
  );
}

// ── V3.5: Reusable Tab Bar component ─────────────────────────────────────────

interface TabItem<T extends string> {
  id: T;
  label: string;
  badge?: string | number;
}

function TabBar<T extends string>({
  tabs,
  active,
  onSelect,
}: {
  tabs: TabItem<T>[];
  active: T;
  onSelect: (id: T) => void;
}) {
  return (
    <div
      role="tablist"
      style={{
        display: 'flex',
        gap: 2,
        borderBottom: '1px solid rgba(46,58,79,0.7)',
        marginBottom: 0,
        flexShrink: 0,
        background: 'rgba(8,12,22,0.6)',
        padding: '0 10px',
      }}
    >
      {tabs.map((tab) => {
        const isActive = active === tab.id;
        return (
          <button
            key={tab.id}
            role="tab"
            aria-selected={isActive}
            onClick={() => onSelect(tab.id)}
            style={{
              padding: '8px 12px',
              background: 'transparent',
              border: 'none',
              borderBottom: isActive ? '2px solid #4C8DFF' : '2px solid transparent',
              color: isActive ? '#6EA8FF' : 'rgba(147,160,180,0.55)',
              fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
              fontSize: 11,
              fontWeight: isActive ? 600 : 400,
              cursor: 'pointer',
              letterSpacing: '0.02em',
              transition: 'color 0.15s, border-color 0.15s',
              display: 'flex',
              alignItems: 'center',
              gap: 5,
              marginBottom: -1,
              outline: 'none',
            }}
            onFocus={(e) => { (e.currentTarget as HTMLButtonElement).style.outline = '1px solid rgba(76,141,255,0.4)'; }}
            onBlur={(e) => { (e.currentTarget as HTMLButtonElement).style.outline = 'none'; }}
          >
            {tab.label}
            {tab.badge !== undefined && tab.badge !== null && (
              <span style={{
                background: isActive ? 'rgba(76,141,255,0.18)' : 'rgba(147,160,180,0.10)',
                color: isActive ? '#6EA8FF' : 'rgba(147,160,180,0.5)',
                borderRadius: 3,
                padding: '0 4px',
                fontSize: 9,
                fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
                fontWeight: 700,
              }}>
                {tab.badge}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

// ── ASTERIA Mission Hero ──────────────────────────────────────────────────────

function AsteriaMissionHero(props: CommonProps) {
  const { experienceManifest: m, linkState: ls, propagationDelayS, queuedDataBits, availableCapacityBits, anomalies } = props;
  if (!m || !ls) return null;

  const queuedVolume = formatBitsAsDataVolume(queuedDataBits);
  const capacityVolume = formatBitsAsDataVolume(availableCapacityBits);
  const queuePressure = availableCapacityBits > 0 ? (queuedDataBits / availableCapacityBits).toFixed(2) : '—';
  const fitFraction = availableCapacityBits > 0 && queuedDataBits > 0
    ? ((availableCapacityBits / queuedDataBits) * 100).toFixed(2)
    : '—';
  const uplink = m.schedule.plan_uplink_margin_s;
  const uplinkLabel = formatDuration(uplink);
  const oneWayLabel = propagationDelayS !== null ? formatDuration(propagationDelayS) : '—';
  const contactLabel = formatDuration(m.schedule.contact_duration_s);

  const snrTrend = presentationSnrTrend(ls.snr_db, m.snr_history);
  const linkStatus = presentationLinkStatus(ls);
  const linkColor = linkStatus === 'CRITICAL' ? '#f87171' : linkStatus === 'DEGRADED' ? '#f59e0b' : '#34d399';

  const thermalAnomaly = anomalies.find((a) => a.anomaly_id === 'ANOM-THERM-017') ?? anomalies[0] ?? null;
  const detectedMinutesAgo = thermalAnomaly ? Math.round(thermalAnomaly.detected_at_s / 60) : null;

  const heroMetrics: Array<{ label: string; value: string; color?: string }> = [
    { label: 'PLAN UPLINK MARGIN', value: uplinkLabel },
    { label: 'QUEUED DATA', value: queuedVolume, color: '#f59e0b' },
    { label: 'CONTACT CAPACITY', value: capacityVolume },
    { label: 'QUEUE PRESSURE', value: `~${queuePressure}×`, color: '#f87171' },
    { label: 'FIT FRACTION', value: `~${fitFraction}%`, color: '#f87171' },
    { label: 'ONE-WAY SIGNAL', value: oneWayLabel },
    { label: 'CONTACT DURATION', value: contactLabel },
    { label: 'ACTIVE EVENT', value: thermalAnomaly?.anomaly_id ?? 'NONE', color: thermalAnomaly ? '#f87171' : undefined },
  ];

  return (
    <div style={{ marginBottom: 12 }}>
      {/* Hero title */}
      <div style={{
        background: 'rgba(8,12,22,0.95)',
        border: '1px solid rgba(76,141,255,0.22)',
        borderRadius: 8,
        padding: '12px 14px',
        marginBottom: 8,
      }}>
        <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9, color: 'rgba(76,141,255,0.7)', letterSpacing: '0.12em', marginBottom: 4 }}>
          MISSION
        </div>
        <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 18, fontWeight: 700, color: '#e2e8f4', letterSpacing: '0.04em' }}>
          {m.display.mission_name}
        </div>
        <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 10, color: '#f59e0b', letterSpacing: '0.1em', marginTop: 2 }}>
          {m.display.scenario_name}
        </div>
        <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 10, color: 'rgba(147,160,180,0.45)', marginTop: 4 }}>
          {m.display.disclaimer}
        </div>
      </div>

      {/* Mission situation metrics grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 8 }}>
        {heroMetrics.map(({ label, value, color }) => (
          <div key={label} style={{
            background: 'rgba(18,24,34,0.7)',
            border: '1px solid rgba(46,58,79,0.8)',
            borderRadius: 6,
            padding: '7px 9px',
          }}>
            <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 8, color: 'rgba(147,160,180,0.55)', letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 2 }}>
              {label}
            </div>
            <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 13, fontWeight: 700, color: color ?? '#e2e8f4' }}>
              {value}
            </div>
          </div>
        ))}
      </div>

      {/* Spacecraft Health */}
      <div style={{ ...CARD, marginBottom: 8 }}>
        <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9, color: 'rgba(147,160,180,0.55)', letterSpacing: '0.1em', marginBottom: 8 }}>
          SPACECRAFT HEALTH
        </div>
        {Object.entries(m.subsystem_status).map(([key, ss]) => {
          const isGood = ss.status === 'nominal' || ss.status === 'stable';
          const color = ss.status === 'degraded' ? '#f59e0b' : ss.status === 'critical' ? '#f87171' : '#34d399';
          return (
            <div key={key} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '4px 0', borderBottom: '1px solid rgba(46,58,79,0.3)' }}>
              <span style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 11, color: 'rgba(147,160,180,0.7)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                {key.replace('_', ' ')}
              </span>
              <div style={{ textAlign: 'right' }}>
                <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 11, fontWeight: 600, color, display: 'block' }}>
                  {ss.label}
                </span>
                {ss.note && (
                  <span style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 10, color: 'rgba(147,160,180,0.45)' }}>
                    {ss.note}
                  </span>
                )}
              </div>
              {isGood}
            </div>
          );
        })}
      </div>

      {/* Active Thermal Event */}
      {thermalAnomaly && (
        <div style={{
          ...CARD,
          border: '1px solid rgba(248,113,113,0.30)',
          background: 'rgba(248,113,113,0.06)',
          marginBottom: 8,
        }}>
          <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9, color: 'rgba(248,113,113,0.7)', letterSpacing: '0.1em', marginBottom: 6 }}>
            DETECTED EVENT
          </div>
          <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 13, fontWeight: 700, color: '#f87171', marginBottom: 2 }}>
            THERMAL ANOMALY
          </div>
          <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 10, color: '#f59e0b', marginBottom: 6 }}>
            {thermalAnomaly.anomaly_id}
          </div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
            <span style={{ background: 'rgba(248,113,113,0.15)', color: '#f87171', fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 9, padding: '2px 7px', borderRadius: 3, border: '1px solid rgba(248,113,113,0.35)' }}>
              ACTIVE
            </span>
            <span style={{ color: 'rgba(147,160,180,0.7)', fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 9 }}>
              SEVERITY {(thermalAnomaly.severity * 100).toFixed(0)}%
            </span>
            {detectedMinutesAgo !== null && (
              <span style={{ color: 'rgba(147,160,180,0.7)', fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 9 }}>
                DETECTED ~{detectedMinutesAgo}m AGO
              </span>
            )}
          </div>
          <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 11, color: 'rgba(147,160,180,0.7)', lineHeight: 1.5 }}>
            {thermalAnomaly.description.slice(0, 200)}{thermalAnomaly.description.length > 200 ? '…' : ''}
          </div>
        </div>
      )}

      {/* Link Health summary */}
      <div style={{ ...CARD, marginBottom: 8 }}>
        <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9, color: 'rgba(147,160,180,0.55)', letterSpacing: '0.1em', marginBottom: 6 }}>
          COMMUNICATION LINK
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 4 }}>
          {[
            { label: 'CURRENT SNR', value: `${ls.snr_db.toFixed(1)} dB` },
            { label: 'TREND', value: snrTrend },
            { label: 'STABILITY', value: `${(ls.link_stability * 100).toFixed(0)}%` },
            { label: 'LINK STATE', value: linkStatus, color: linkColor },
          ].map(({ label, value, color }) => (
            <div key={label} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 4, padding: '4px 6px' }}>
              <div style={{ color: 'rgba(147,160,180,0.55)', fontSize: 8, fontFamily: '"IBM Plex Mono", ui-monospace', marginBottom: 2 }}>{label}</div>
              <div style={{ color: color ?? '#e2e8f4', fontSize: 10, fontFamily: '"IBM Plex Mono", ui-monospace', fontWeight: 600 }}>{value}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Mission section ───────────────────────────────────────────────────────────

function MissionSection(props: CommonProps) {
  const ms = props.missionState;
  const ls = props.linkState;
  const dpCount = props.dataProductsCount;
  const anomCount = props.anomalies.length;

  // When ASTERIA experience is available, show the hero panel instead of the generic context
  if (props.experienceAvailable && props.experienceManifest) {
    return (
      <>
        <AsteriaMissionHero {...props} />
        <ResizableSection title="Mission State" icon="◉" accent="#4C8DFF">
          {ms ? (
            <TableScroll>
              <MissionStatePanel missionState={ms} />
            </TableScroll>
          ) : (
            <div style={{ color: 'rgba(147,160,180,0.5)', fontSize: 12 }}>No mission data</div>
          )}
        </ResizableSection>
        {ls && (
          <ResizableSection title="Comm Budget" icon="⌾" accent="#4C8DFF">
            <CommBudgetBar
              availableCapacityBits={props.availableCapacityBits}
              queuedDataBits={props.queuedDataBits}
              dataProductsCount={props.dataProductsCount}
              remainingWindowS={ls.remaining_window_s}
            />
          </ResizableSection>
        )}
      </>
    );
  }

  // Generic scenario fallback
  return (
    <>
      {ms && ls && (
        <StatGrid items={[
          { label: 'Window', value: `${ms.comm_window_remaining_s.toFixed(0)} s`, color: ms.comm_window_remaining_s < 60 ? '#f87171' : '#34d399' },
          { label: 'Risk', value: ms.risk_level, color: ms.risk_level === 'CRITICAL' ? '#f87171' : ms.risk_level === 'HIGH' ? '#fb923c' : ms.risk_level === 'MEDIUM' ? '#f59e0b' : '#34d399' },
          { label: 'SNR', value: `${ls.snr_db.toFixed(1)} dB`, color: ls.snr_db < 5 ? '#f87171' : ls.snr_db < 10 ? '#f59e0b' : '#34d399' },
          { label: 'Stability', value: `${(ls.link_stability * 100).toFixed(0)}%`, color: ls.link_stability < 0.5 ? '#f87171' : ls.link_stability < 0.75 ? '#f59e0b' : '#34d399' },
        ]} />
      )}

      {props.decisionMode === 'unselected' && dpCount > 0 && (
        <div style={{ ...CARD, borderColor: 'rgba(76,141,255,0.22)', background: 'rgba(8,12,22,0.85)', marginBottom: 10 }}>
          <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9, color: 'rgba(76,141,255,0.7)', letterSpacing: '0.1em', marginBottom: 10 }}>
            MISSION CONTEXT
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: 'rgba(147,160,180,0.8)' }}>Data products</span>
              <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 14, fontWeight: 700, color: '#f59e0b' }}>{dpCount}</span>
            </div>
            {anomCount > 0 && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: 'rgba(147,160,180,0.8)' }}>Active anomalies</span>
                <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 14, fontWeight: 700, color: '#f87171' }}>{anomCount}</span>
              </div>
            )}
            {ms && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: 'rgba(147,160,180,0.8)' }}>Comm window</span>
                <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 14, fontWeight: 700, color: '#34d399' }}>{ms.comm_window_remaining_s.toFixed(0)} s</span>
              </div>
            )}
            {ls && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: 'rgba(147,160,180,0.8)' }}>Link status</span>
                <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 12, fontWeight: 600, color: ls.link_stability > 0.7 ? '#34d399' : '#f59e0b' }}>
                  {ls.link_stability > 0.85 ? 'Stable' : ls.link_stability > 0.6 ? 'Degraded' : 'Unstable'}
                </span>
              </div>
            )}
          </div>
          <div style={{ marginTop: 10, paddingTop: 8, borderTop: '1px solid rgba(46,58,79,0.5)', fontFamily: '"IBM Plex Sans", system-ui', fontSize: 11, color: 'rgba(147,160,180,0.5)', lineHeight: 1.5 }}>
            No transmission plan has been created yet. Choose a decision mode below or navigate to the AI or Data sections.
          </div>
        </div>
      )}

      <ResizableSection title="Mission State" icon="◉" accent="#4C8DFF">
        {ms ? (
          <TableScroll>
            <MissionStatePanel missionState={ms} />
          </TableScroll>
        ) : (
          <div style={{ color: 'rgba(147,160,180,0.5)', fontSize: 12 }}>No mission data</div>
        )}
      </ResizableSection>

      {props.linkState && (
        <ResizableSection title="Comm Budget" icon="⌾" accent="#4C8DFF">
          <CommBudgetBar
            availableCapacityBits={props.availableCapacityBits}
            queuedDataBits={props.queuedDataBits}
            dataProductsCount={props.dataProductsCount}
            remainingWindowS={props.linkState.remaining_window_s}
          />
        </ResizableSection>
      )}

      {props.anomalies.length > 0 && (
        <ResizableSection title="Anomalies" icon="⚠" accent="#f87171">
          {props.anomalies.map((a) => (
            <div key={a.anomaly_id} style={{
              display: 'flex', gap: 10, alignItems: 'flex-start',
              padding: '7px 0', borderBottom: '1px solid rgba(46,58,79,0.5)',
              minWidth: 0,
            }}>
              <span style={{ color: '#f87171', flexShrink: 0, fontSize: 11, marginTop: 1 }}>
                {a.severity >= 0.75 ? '●' : '○'}
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{
                  color: '#e2e8f4', fontWeight: 600, fontSize: 12,
                  fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
                  wordBreak: 'break-all',
                }}>{a.anomaly_id}</div>
                <div style={{ color: 'rgba(147,160,180,0.8)', fontSize: 11, marginTop: 3, lineHeight: 1.45 }}>{a.description}</div>
                <div style={{ color: 'rgba(147,160,180,0.45)', fontSize: 10, marginTop: 2 }}>
                  {a.subsystem} · severity {(a.severity * 100).toFixed(0)}%
                </div>
              </div>
            </div>
          ))}
        </ResizableSection>
      )}
    </>
  );
}

// ── Spacecraft section ────────────────────────────────────────────────────────

function SpacecraftSection(props: CommonProps) {
  return (
    <ResizableSection title="Spacecraft Geometry" icon="⬡" accent="#4C8DFF">
      <TableScroll>
        <SignalGeometryBlock
          distanceKm={props.distanceKm}
          propagationDelayS={props.propagationDelayS}
          roundTripTimeS={props.roundTripTimeS}
        />
      </TableScroll>
    </ResizableSection>
  );
}

// ── Communications section ────────────────────────────────────────────────────

function CommsSection(props: CommonProps) {
  if (!props.linkState) return (
    <div style={{ ...CARD }}>
      <div style={{ color: 'rgba(147,160,180,0.5)', fontSize: 12 }}>No link data available</div>
    </div>
  );
  return (
    <ResizableSection title="Link Health" icon="⌾" accent="#4C8DFF">
      <div style={{ minWidth: 0 }}>
        <LinkHealthPanel
          linkState={props.linkState}
          snrHistory={props.experienceManifest?.snr_history}
          onWhatIfResult={props.onWhatIfResult}
        />
      </div>
    </ResizableSection>
  );
}

// ── Data section — V3.5 workspace-aware data browser ─────────────────────────

const FILTER_LABELS: Record<string, string> = {
  all: 'All',
  critical: 'Critical',
  required: 'Required',
  anomaly: 'Anomaly',
  telemetry: 'Telemetry',
  diagnostic: 'Diagnostic',
  science: 'Science',
  image: 'Image',
  housekeeping: 'Housekeeping',
  navigation: 'Nav',
};

type SortKey = 'criticality' | 'deadline_s' | 'size_bits' | 'age_s' | 'mission_relevance';

const PAGE_SIZE = 20;

function DataSection(props: CommonProps) {
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<string>('all');
  const [sortKey, setSortKey] = useState<SortKey>('criticality');
  const [sortDesc, setSortDesc] = useState(true);
  const [page, setPage] = useState(0);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const products = props.rawDataProducts;
  const hasDP = props.hasDataProducts;
  const isLegacy = !hasDP && (props.dataProductsCount === 0);
  const wm = props.workspaceMode;

  // Derive available filter categories from actual data
  const availableTypes = useMemo(() => {
    const types = new Set(products.map((p) => p.product_type));
    return Array.from(types);
  }, [products]);

  const hasAnomaly = useMemo(() => products.some((p) => p.anomaly_id), [products]);
  const hasRequired = useMemo(() => products.some((p) => p.delivery_requirement === 'required'), [products]);

  // Filtered + sorted products
  const filtered = useMemo(() => {
    let list = products;
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter((p) =>
        p.product_id.toLowerCase().includes(q) ||
        p.product_type.toLowerCase().includes(q) ||
        p.subsystem.toLowerCase().includes(q) ||
        (p.anomaly_id?.toLowerCase().includes(q) ?? false) ||
        p.description.toLowerCase().includes(q)
      );
    }
    if (filter !== 'all') {
      if (filter === 'critical') list = list.filter((p) => p.criticality >= 0.7);
      else if (filter === 'required') list = list.filter((p) => p.delivery_requirement === 'required');
      else if (filter === 'anomaly') list = list.filter((p) => p.anomaly_id !== null);
      else list = list.filter((p) => p.product_type === filter);
    }
    list = [...list].sort((a, b) => {
      const va = a[sortKey] as number;
      const vb = b[sortKey] as number;
      return sortDesc ? vb - va : va - vb;
    });
    return list;
  }, [products, search, filter, sortKey, sortDesc]);

  const totalPages = Math.ceil(filtered.length / PAGE_SIZE);
  const paginated = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const handleSearch = useCallback((v: string) => { setSearch(v); setPage(0); }, []);
  const handleFilter = useCallback((v: string) => { setFilter(v); setPage(0); }, []);
  const handleSort = useCallback((key: SortKey) => {
    setSortKey((prev) => {
      if (prev === key) { setSortDesc((d) => !d); return key; }
      setSortDesc(true);
      return key;
    });
    setPage(0);
  }, []);

  if (isLegacy) {
    return (
      <div style={{ ...CARD, borderColor: 'rgba(245,158,11,0.28)', background: 'rgba(245,158,11,0.04)' }}>
        <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9, color: '#f59e0b', letterSpacing: '0.1em', marginBottom: 8 }}>
          LEGACY PACKET SCENARIO
        </div>
        <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: 'rgba(147,160,180,0.8)', lineHeight: 1.55, marginBottom: 10 }}>
          This scenario uses the legacy packet model. AI data-product prioritization and high-volume manual planning are unavailable.
        </div>
        {props.queue && (
          <ResizableSection title="Transmission Queue" icon="▦" accent="#4C8DFF">
            <div style={{ overflowX: 'auto', minWidth: 0 }}>
              <TransmissionQueuePanel plan={props.queue} />
            </div>
          </ResizableSection>
        )}
      </div>
    );
  }

  const selectedCount = props.manualSelectedIds.size;
  const selectedBits = props.manualOrder.reduce((sum, id) => {
    const p = products.find((dp) => dp.product_id === id);
    return sum + (p?.size_bits ?? 0);
  }, 0);
  const capacityUsedPct = props.availableCapacityBits > 0
    ? Math.min(100, (selectedBits / props.availableCapacityBits) * 100)
    : 0;

  // Expanded/Focus: show inline summary bar instead of card
  const showExpandedColumns = wm === 'expanded' || wm === 'focus';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
      {/* Toolbar */}
      <div style={{ padding: '8px 0 6px', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
          <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 10, color: '#f59e0b', fontWeight: 700 }}>
            DATA PRODUCTS
          </span>
          <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 16, fontWeight: 700, color: '#f59e0b' }}>
            {products.length}
          </span>
        </div>
        <input
          type="text"
          placeholder="Search products, subsystem, anomaly…"
          value={search}
          onChange={(e) => handleSearch(e.target.value)}
          style={{
            width: '100%', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(46,58,79,0.8)',
            color: '#e2e8f4', borderRadius: 6, padding: '6px 10px', fontSize: 12,
            fontFamily: '"IBM Plex Mono", ui-monospace, monospace', boxSizing: 'border-box',
            marginBottom: 5,
          }}
        />
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3, marginBottom: 5 }}>
          {['all', hasRequired && 'required', hasAnomaly && 'anomaly', 'critical', ...availableTypes]
            .filter(Boolean)
            .filter((v, i, a) => a.indexOf(v) === i)
            .slice(0, 10)
            .map((f) => {
              const fStr = f as string;
              const activeF = filter === fStr;
              return (
                <button key={fStr} onClick={() => handleFilter(fStr)} style={{
                  fontSize: 10, padding: '3px 8px',
                  background: activeF ? 'rgba(76,141,255,0.15)' : 'rgba(255,255,255,0.03)',
                  color: activeF ? '#6EA8FF' : 'rgba(147,160,180,0.6)',
                  border: `1px solid ${activeF ? 'rgba(76,141,255,0.35)' : 'rgba(46,58,79,0.7)'}`,
                  borderRadius: 4, cursor: 'pointer', fontFamily: '"IBM Plex Sans", system-ui',
                  fontWeight: activeF ? 600 : 400,
                }}>
                  {FILTER_LABELS[fStr] ?? fStr}
                </button>
              );
            })}
        </div>
        <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: 'rgba(147,160,180,0.4)', marginRight: 2 }}>SORT</span>
          {(['criticality', 'deadline_s', 'size_bits', 'age_s', 'mission_relevance'] as SortKey[]).map((key) => {
            const labels: Record<SortKey, string> = { criticality: 'Crit', deadline_s: 'Deadline', size_bits: 'Size', age_s: 'Age', mission_relevance: 'Relevance' };
            const activeS = sortKey === key;
            return (
              <button key={key} onClick={() => handleSort(key)} style={{
                fontSize: 10, padding: '2px 7px',
                background: activeS ? 'rgba(76,141,255,0.12)' : 'transparent',
                color: activeS ? '#6EA8FF' : 'rgba(147,160,180,0.5)',
                border: `1px solid ${activeS ? 'rgba(76,141,255,0.28)' : 'rgba(46,58,79,0.5)'}`,
                borderRadius: 4, cursor: 'pointer', fontFamily: '"IBM Plex Sans"',
              }}>
                {labels[key]}{activeS ? (sortDesc ? ' ↓' : ' ↑') : ''}
              </button>
            );
          })}
          <span style={{ marginLeft: 'auto', fontFamily: '"IBM Plex Mono"', fontSize: 9, color: 'rgba(147,160,180,0.4)' }}>
            {filtered.length}/{products.length}
            {selectedCount > 0 && ` · ${selectedCount} sel`}
          </span>
        </div>
      </div>

      {/* Product list — natural height (pagination limits to PAGE_SIZE rows; Main Control scrolls) */}
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2, paddingBottom: selectedCount > 0 ? 60 : 8 }}>
          {paginated.map((p) => {
            const isSelected = props.manualSelectedIds.has(p.product_id);
            const isExp = expandedId === p.product_id;
            const rank = props.manualOrder.indexOf(p.product_id);
            return (
              <div key={p.product_id} style={{
                background: isSelected ? 'rgba(52,211,153,0.06)' : 'rgba(255,255,255,0.02)',
                border: `1px solid ${isSelected ? 'rgba(52,211,153,0.25)' : 'rgba(46,58,79,0.6)'}`,
                borderRadius: 6,
                overflow: 'hidden',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 8px', cursor: 'pointer' }}
                  onClick={() => setExpandedId(isExp ? null : p.product_id)}>
                  {props.decisionMode === 'manual' && (
                    <div
                      onClick={(e) => { e.stopPropagation(); props.onToggleManualSelect(p.product_id); }}
                      style={{
                        width: 14, height: 14, borderRadius: 3, flexShrink: 0, cursor: 'pointer',
                        background: isSelected ? '#34d399' : 'transparent',
                        border: `1px solid ${isSelected ? '#34d399' : 'rgba(147,160,180,0.4)'}`,
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                      }}
                    >
                      {isSelected && <span style={{ color: '#000', fontSize: 9, fontWeight: 700 }}>✓</span>}
                    </div>
                  )}
                  {isSelected && rank >= 0 && (
                    <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#34d399', minWidth: 16, textAlign: 'right', flexShrink: 0 }}>#{rank + 1}</span>
                  )}
                  {p.anomaly_id && (
                    <span style={{ color: '#f87171', fontSize: 8, fontFamily: '"IBM Plex Mono"', fontWeight: 700, flexShrink: 0 }}>⚠</span>
                  )}
                  <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 11, color: '#6EA8FF', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {p.product_id}
                  </span>
                  {showExpandedColumns && (
                    <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: 'rgba(147,160,180,0.5)', flexShrink: 0, minWidth: 52, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {p.subsystem}
                    </span>
                  )}
                  {showExpandedColumns && (
                    <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: 'rgba(147,160,180,0.5)', flexShrink: 0, minWidth: 40 }}>
                      {formatBitsAsDataVolume(p.size_bits)}
                    </span>
                  )}
                  <div style={{ width: 28, height: 3, background: 'rgba(46,58,79,0.8)', borderRadius: 2, flexShrink: 0 }}>
                    <div style={{ width: `${p.criticality * 100}%`, height: '100%', borderRadius: 2, background: p.criticality >= 0.85 ? '#f87171' : p.criticality >= 0.7 ? '#f59e0b' : '#34d399' }} />
                  </div>
                  <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: p.deadline_s < 120 ? '#f87171' : 'rgba(147,160,180,0.5)', flexShrink: 0, minWidth: 36, textAlign: 'right' }}>
                    {p.deadline_s < 3600 ? `${p.deadline_s.toFixed(0)}s` : `${(p.deadline_s / 3600).toFixed(1)}h`}
                  </span>
                  {showExpandedColumns && (
                    <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: p.mission_relevance > 0.7 ? '#34d399' : 'rgba(147,160,180,0.4)', flexShrink: 0, minWidth: 28 }}>
                      {(p.mission_relevance * 100).toFixed(0)}%
                    </span>
                  )}
                  <span style={{ color: 'rgba(147,160,180,0.3)', fontSize: 9, flexShrink: 0 }}>{isExp ? '▲' : '▼'}</span>
                </div>
                {isExp && (
                  <div style={{ padding: '8px 10px 10px', borderTop: '1px solid rgba(46,58,79,0.5)', background: 'rgba(0,0,0,0.15)' }}>
                    <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: 'rgba(147,160,180,0.8)', lineHeight: 1.55, marginBottom: 8 }}>
                      {p.description || '—'}
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '3px 12px', fontSize: 10 }}>
                      {[
                        ['Type', p.product_type],
                        ['Subsystem', p.subsystem],
                        ['Size', formatBitsAsDataVolume(p.size_bits)],
                        ['Criticality', p.criticality.toFixed(2)],
                        ['Relevance', p.mission_relevance.toFixed(2)],
                        ['Delivery', p.delivery_requirement],
                        ...(p.anomaly_id ? [['Anomaly', p.anomaly_id]] : []),
                        ...(p.experiment_id ? [['Experiment', p.experiment_id]] : []),
                      ].map(([label, val]) => (
                        <div key={label} style={{ display: 'flex', gap: 4 }}>
                          <span style={{ color: 'rgba(147,160,180,0.4)', fontFamily: '"IBM Plex Sans"' }}>{label}</span>
                          <span style={{ color: '#e2e8f4', fontFamily: '"IBM Plex Mono"' }}>{val}</span>
                        </div>
                      ))}
                    </div>
                    {props.decisionMode === 'manual' && (
                      <button
                        onClick={() => props.onToggleManualSelect(p.product_id)}
                        style={{
                          marginTop: 8, fontSize: 11, padding: '4px 12px',
                          background: isSelected ? 'rgba(248,113,113,0.1)' : 'rgba(52,211,153,0.1)',
                          color: isSelected ? '#f87171' : '#34d399',
                          border: `1px solid ${isSelected ? 'rgba(248,113,113,0.3)' : 'rgba(52,211,153,0.25)'}`,
                          borderRadius: 5, cursor: 'pointer', fontFamily: '"IBM Plex Sans"',
                        }}
                      >
                        {isSelected ? 'Deselect' : 'Select for transmission'}
                      </button>
                    )}
                  </div>
                )}
              </div>
            );
          })}
          {paginated.length === 0 && (
            <div style={{ color: 'rgba(147,160,180,0.4)', fontSize: 12, padding: '12px 0', textAlign: 'center' }}>
              No products match the current filter.
            </div>
          )}
        </div>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'center', padding: '6px 0', borderTop: '1px solid rgba(46,58,79,0.5)', flexShrink: 0 }}>
          <button onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}
            style={{ fontSize: 10, padding: '3px 8px', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(46,58,79,0.7)', borderRadius: 4, color: 'rgba(147,160,180,0.7)', cursor: 'pointer' }}>
            ←
          </button>
          <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 10, color: 'rgba(147,160,180,0.6)' }}>
            {page + 1} / {totalPages}
          </span>
          <button onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))} disabled={page >= totalPages - 1}
            style={{ fontSize: 10, padding: '3px 8px', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(46,58,79,0.7)', borderRadius: 4, color: 'rgba(147,160,180,0.7)', cursor: 'pointer' }}>
            →
          </button>
        </div>
      )}

      {/* Sticky selection summary bar — manual mode with selection */}
      {props.decisionMode === 'manual' && selectedCount > 0 && (
        <div style={{
          borderTop: '1px solid rgba(52,211,153,0.2)',
          background: 'rgba(8,14,24,0.97)',
          padding: '8px 10px',
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          flexShrink: 0,
        }}>
          <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 10, color: '#34d399', fontWeight: 700 }}>{selectedCount}</span>
          <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: 'rgba(147,160,180,0.6)' }}>selected</span>
          <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 10, color: 'rgba(147,160,180,0.6)' }}>{formatBitsAsDataVolume(selectedBits)}</span>
          <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 10, color: capacityUsedPct > 90 ? '#f87171' : 'rgba(147,160,180,0.5)' }}>{capacityUsedPct.toFixed(0)}% cap</span>
          <button
            onClick={props.onClearManualSelection}
            style={{ marginLeft: 'auto', fontSize: 10, padding: '3px 8px', background: 'transparent', color: 'rgba(147,160,180,0.6)', border: '1px solid rgba(46,58,79,0.7)', borderRadius: 4, cursor: 'pointer', fontFamily: '"IBM Plex Sans"' }}
          >
            Clear
          </button>
        </div>
      )}
    </div>
  );
}

// ── F3: Human Decision Panel (Approve / Modify / Reject) ─────────────────────

function AiHumanDecisionPanel({ props }: { props: CommonProps }) {
  const rec = props.recommendation;
  const recEval = props.recEval;
  const recPlan = props.recPlan;
  const approvalPhase = props.approvalPhase;
  const isTransmitting = approvalPhase === 'transmitting';
  const isComplete = approvalPhase === 'complete';

  if (!rec) return null;

  const planPayloadBits = (recPlan?.packets ?? []).reduce((s, p) => s + p.size_bits, 0);
  const deferredCount = recEval?.deferred_packets.length ?? 0;
  const selectedCount = (recPlan?.packets.length ?? 0);

  const riskLevel = recEval?.risk_level ?? rec.risk_level;
  const riskColor =
    riskLevel === 'LOW' ? '#34d399' :
    riskLevel === 'MEDIUM' ? '#f59e0b' :
    riskLevel === 'HIGH' ? '#fb923c' : '#f87171';

  // Required delivery rate from evaluation
  const reqDeliveryRate = recEval
    ? recEval.critical_packets_delivered / Math.max(1, recEval.total_critical_packets)
    : null;

  // Anomaly-linked packet coverage from plan packets
  const anomalyIds = new Set(props.anomalies.map((a) => a.anomaly_id));
  const anomalyLinkedPackets = (recPlan?.packets ?? []).filter((pkt) => {
    const dp = props.rawDataProducts.find((p) => p.product_id === pkt.packet_id);
    return dp?.anomaly_id && anomalyIds.has(dp.anomaly_id);
  });
  const totalAnomalyLinked = props.rawDataProducts.filter((p) => p.anomaly_id && anomalyIds.has(p.anomaly_id)).length;
  const anomalyCoverage = totalAnomalyLinked > 0
    ? (anomalyLinkedPackets.length / totalAnomalyLinked)
    : null;

  return (
    <div style={{
      background: 'rgba(8,12,22,0.95)',
      border: '1px solid rgba(76,141,255,0.18)',
      borderRadius: 6, padding: '12px 14px',
      marginBottom: 12,
    }}>
      {/* Header */}
      <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9, color: 'rgba(76,141,255,0.7)', letterSpacing: '0.1em', marginBottom: 8 }}>
        AI RECOMMENDATION
      </div>

      {/* Metrics grid — truthful labels (see Phase 5.1D WorkStream G) */}
      {/* selectedCount = recPlan.packets.length = the full prioritized plan queue */}
      {/* projectedFit = packets NOT deferred according to evaluation */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 12 }}>
        {[
          { label: 'PRIORITIZED QUEUE', value: `${selectedCount} products`, color: '#6EA8FF' },
          { label: 'PROJECTED THIS CONTACT', value: deferredCount < selectedCount ? `${selectedCount - deferredCount} products` : `${selectedCount} products`, color: '#34d399' },
          { label: 'PRIORITY PAYLOAD', value: formatBitsAsDataVolume(planPayloadBits), color: '#e2e8f4' },
          { label: 'CONTACT CAPACITY', value: formatBitsAsDataVolume(props.availableCapacityBits), color: 'rgba(147,160,180,0.7)' },
          { label: 'PLAN RISK', value: riskLevel, color: riskColor },
          { label: 'PROJECTED DEFERRED', value: `${deferredCount}`, color: deferredCount > 0 ? '#f59e0b' : '#34d399' },
          ...(reqDeliveryRate !== null ? [{ label: 'REQ. DELIVERY', value: `${(reqDeliveryRate * 100).toFixed(0)}%`, color: reqDeliveryRate >= 0.8 ? '#34d399' : '#f59e0b' }] : []),
          ...(anomalyCoverage !== null ? [{ label: 'ANOMALY COVERAGE', value: `${(anomalyCoverage * 100).toFixed(0)}%`, color: anomalyCoverage >= 0.8 ? '#34d399' : anomalyCoverage >= 0.5 ? '#f59e0b' : '#f87171' }] : []),
        ].map(({ label, value, color }) => (
          <div key={label} style={{
            background: 'rgba(255,255,255,0.025)',
            border: '1px solid rgba(46,58,79,0.5)',
            borderRadius: 4, padding: '5px 8px',
          }}>
            <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: 'rgba(147,160,180,0.45)', letterSpacing: '0.07em', marginBottom: 2 }}>{label}</div>
            <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 12, fontWeight: 700, color }}>{value}</div>
          </div>
        ))}
      </div>

      {/* Decision buttons */}
      {!isComplete && !isTransmitting && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <button
            onClick={props.onApproveAiPlan}
            disabled={isTransmitting}
            style={{
              width: '100%', padding: '9px 0', fontSize: 12, fontWeight: 600,
              fontFamily: '"IBM Plex Sans", system-ui', cursor: 'pointer',
              background: 'rgba(52,211,153,0.12)', color: '#34d399',
              border: '1px solid rgba(52,211,153,0.35)', borderRadius: 6,
              letterSpacing: '0.02em',
            }}
          >
            ✓ APPROVE TRANSMISSION
          </button>
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              onClick={props.onModifyAiPlan}
              style={{
                flex: 1, padding: '7px 0', fontSize: 11, fontWeight: 600,
                fontFamily: '"IBM Plex Sans"', cursor: 'pointer',
                background: 'rgba(245,158,11,0.08)', color: '#f59e0b',
                border: '1px solid rgba(245,158,11,0.28)', borderRadius: 5,
              }}
            >
              ✎ MODIFY PLAN
            </button>
            <button
              onClick={props.onRejectAiPlan}
              style={{
                flex: 1, padding: '7px 0', fontSize: 11, fontWeight: 600,
                fontFamily: '"IBM Plex Sans"', cursor: 'pointer',
                background: 'rgba(248,113,113,0.08)', color: '#f87171',
                border: '1px solid rgba(248,113,113,0.25)', borderRadius: 5,
              }}
            >
              ✕ REJECT
            </button>
          </div>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 10, color: 'rgba(147,160,180,0.4)', textAlign: 'center', marginTop: 2 }}>
            Approve authorizes transmission · Modify seeds manual planning · Reject does not transmit
          </div>
        </div>
      )}

      {isTransmitting && (
        <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 11, color: '#6EA8FF', textAlign: 'center', padding: '8px 0' }}>
          Transmission in progress…
        </div>
      )}
      {isComplete && (
        <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 11, color: '#34d399', textAlign: 'center', padding: '8px 0' }}>
          ✓ Transmission complete — see Log section
        </div>
      )}
    </div>
  );
}

// ── V3.5 / F3: AI Mission Triage helpers ─────────────────────────────────────

/** Determine provider-aware triage heading */
function triageHeading(providerName: string | null, fallbackReason: string | null): {
  title: string; subtitle: string | null; isLocal: boolean;
} {
  const isLocal = !providerName
    || providerName.toLowerCase().includes('local')
    || providerName.toLowerCase().includes('rule')
    || providerName.toLowerCase().includes('deterministic');
  const hasFallback = !!fallbackReason;
  if (isLocal || hasFallback) {
    return {
      title: 'DETERMINISTIC MISSION TRIAGE',
      subtitle: 'LOCAL FALLBACK',
      isLocal: true,
    };
  }
  return {
    title: 'AI MISSION TRIAGE',
    subtitle: null,
    isLocal: false,
  };
}

/** WHY THIS MATTERS panel — shows RankedProduct reason + DataProduct evidence */
function WhyThisMatters({
  rp,
  dataProduct,
  anomaly,
}: {
  rp: RankedProduct;
  dataProduct: DataProduct | undefined;
  anomaly: AnomalyEvent | undefined;
}) {
  const AI_COLOR = '#6EA8FF';
  const MUTED = 'rgba(147,160,180,0.8)';
  const DIM = 'rgba(147,160,180,0.45)';

  return (
    <div style={{
      background: 'rgba(8,12,22,0.95)',
      border: '1px solid rgba(76,141,255,0.18)',
      borderRadius: 6, padding: '10px 12px',
      marginBottom: 8,
    }}>
      {/* WHY THIS MATTERS header */}
      <div style={{
        fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9,
        color: 'rgba(76,141,255,0.7)', letterSpacing: '0.1em', marginBottom: 6,
        display: 'flex', alignItems: 'center', gap: 6,
      }}>
        <span>◈</span> WHY THIS MATTERS
        <span style={{ marginLeft: 'auto', fontFamily: '"IBM Plex Mono"', fontSize: 8, color: DIM }}>
          Rank #{rp.priority}
        </span>
      </div>

      {/* Product ID */}
      <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 13, fontWeight: 700, color: AI_COLOR, marginBottom: 4 }}>
        {rp.product_id}
      </div>

      {/* AI reasoning — labelled advisory */}
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: DIM, letterSpacing: '0.08em', marginBottom: 3 }}>
          AI INTERPRETATION · ADVISORY · UNCALIBRATED
        </div>
        <div style={{
          fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: MUTED,
          fontStyle: 'italic', lineHeight: 1.5,
          background: 'rgba(0,0,0,0.12)', borderRadius: 3, padding: '5px 8px',
        }}>
          "{rp.reason}"
        </div>
      </div>

      {/* Authoritative evidence from DataProduct */}
      {dataProduct && (
        <div>
          <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: 'rgba(52,211,153,0.7)', letterSpacing: '0.08em', marginBottom: 5 }}>
            ● AUTHORITATIVE EVIDENCE
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '3px 10px', fontSize: 10 }}>
            {[
              ['Active event', anomaly ? anomaly.anomaly_id : (dataProduct.anomaly_id ?? '—')],
              ['Subsystem', dataProduct.subsystem],
              ['Severity', anomaly ? `${(anomaly.severity * 100).toFixed(0)}%` : '—'],
              ['Criticality', dataProduct.criticality.toFixed(2)],
              ['Mission relevance', dataProduct.mission_relevance.toFixed(2)],
              ['Delivery req.', dataProduct.delivery_requirement],
              ['Payload', formatBitsAsDataVolume(dataProduct.size_bits)],
              ['Contact deadline', dataProduct.deadline_s < 3600 ? `${dataProduct.deadline_s.toFixed(0)} s` : `${(dataProduct.deadline_s / 3600).toFixed(1)} h`],
            ].map(([label, val]) => (
              <div key={label} style={{ display: 'flex', gap: 4 }}>
                <span style={{ color: 'rgba(147,160,180,0.4)', fontFamily: '"IBM Plex Sans"' }}>{label}</span>
                <span style={{ color: '#e2e8f4', fontFamily: '"IBM Plex Mono"', wordBreak: 'break-all' }}>{val}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** AI Funnel display: 1,284 → 50 → 23 → N */
function AiFunnel({
  totalQueued,
  semanticCandidates,
  urgentCount,
  projectedFit,
}: {
  totalQueued: number;
  semanticCandidates: number;
  urgentCount: number;
  projectedFit: number | null;
}) {
  const DIM = 'rgba(147,160,180,0.45)';
  const rows: Array<{ count: number; label: string; color: string }> = [
    { count: totalQueued, label: 'QUEUED PRODUCTS', color: '#f59e0b' },
    { count: semanticCandidates, label: 'SEMANTIC CANDIDATES', color: '#6EA8FF' },
    { count: urgentCount, label: 'URGENT / OPERATIONALLY RELEVANT', color: '#f87171' },
    { count: projectedFit ?? 0, label: 'PROJECTED TO FIT CONTACT', color: '#34d399' },
  ];

  return (
    <div style={{
      background: 'rgba(8,12,22,0.95)',
      border: '1px solid rgba(46,58,79,0.7)',
      borderRadius: 6, padding: '10px 12px',
      marginBottom: 10,
    }}>
      <div style={{
        fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9,
        color: 'rgba(147,160,180,0.55)', letterSpacing: '0.1em', marginBottom: 8,
      }}>
        MISSION TRIAGE FUNNEL
      </div>
      {rows.map((row, i) => (
        <div key={row.label}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 20, fontWeight: 700, color: row.color, minWidth: 56, textAlign: 'right' }}>
              {row.count > 0 || i === 3 ? row.count.toLocaleString() : '—'}
            </span>
            <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9, color: row.color, letterSpacing: '0.06em', flexShrink: 0 }}>
              {row.label}
            </span>
          </div>
          {i < rows.length - 1 && (
            <div style={{ marginLeft: 24, color: DIM, fontSize: 11, lineHeight: '14px' }}>↓</div>
          )}
        </div>
      ))}
    </div>
  );
}

// ── V3.5: AI Copilot section — Tabbed workspace ───────────────────────────────

type AiTab = 'prioritization' | 'reasoning' | 'decision';

function AiSection(props: CommonProps) {
  const [activeTab, setActiveTab] = useState<AiTab>('prioritization');
  const [selectedRpId, setSelectedRpId] = useState<string | null>(null);

  const lc = props.aiLifecycle;
  const isStandby = lc === 'standby';
  const isAnalyzing = lc === 'analyzing';
  const isReady = lc === 'ready';
  const isError = lc === 'error';
  const isStale = lc === 'stale';
  const hasResult = isReady || isStale;
  const dp = props.dataProductsCount;
  const anomCount = props.anomalies.length;
  const ms = props.missionState;
  const ls = props.linkState;

  const statusColor = isAnalyzing ? '#4C8DFF' : isReady ? '#34d399' : isError ? '#f87171' : isStale ? '#f59e0b' : 'rgba(147,160,180,0.35)';
  const statusLabel = isAnalyzing ? 'ANALYZING' : isReady ? 'READY' : isError ? 'FAILED' : isStale ? 'STALE' : 'STANDBY';
  const notInAiMode = props.decisionMode !== 'ai';

  const rankedCount = props.aiPrioritization?.ranked_products.length ?? null;

  // ── Derived funnel counts ────────────────────────────────────────────────────
  const funnelData = useMemo(() => {
    const totalQueued = props.rawDataProducts.length > 0 ? props.rawDataProducts.length : props.dataProductsCount;
    const semanticCandidates = props.aiCandidateCount ?? props.aiPrioritization?.candidate_count ?? 0;

    // Urgent count: apply production predicate to candidates
    // Candidates are products that appear in ranked_products
    const rankedIds = new Set((props.aiPrioritization?.ranked_products ?? []).map((r) => r.product_id));
    const candidateProducts = props.rawDataProducts.filter((p) => rankedIds.has(p.product_id));
    const anomalyIds = new Set(props.anomalies.map((a) => a.anomaly_id));
    const effectiveWindowS = ms?.comm_window_remaining_s ?? 0;
    const urgentCount = countUrgentProducts(candidateProducts, anomalyIds, effectiveWindowS);

    // Projected fit: ai_plan packets that are NOT deferred
    const recPlanPackets = props.recPlan?.packets ?? [];
    const deferredSet = new Set(props.recEval?.deferred_packets ?? []);
    const projectedFit = recPlanPackets.filter((pkt) => !deferredSet.has(pkt.packet_id)).length;

    return { totalQueued, semanticCandidates, urgentCount, projectedFit: projectedFit > 0 ? projectedFit : null };
  }, [props.rawDataProducts, props.dataProductsCount, props.aiCandidateCount, props.aiPrioritization, props.anomalies, ms, props.recPlan, props.recEval]);

  // ── Derived provider heading ─────────────────────────────────────────────────
  const triageInfo = useMemo(
    () => triageHeading(
      props.aiActualProvider ?? props.aiProvider,
      props.aiRecommendationFallbackReason ?? props.aiPrioritizationFallbackReason,
    ),
    [props.aiActualProvider, props.aiProvider, props.aiRecommendationFallbackReason, props.aiPrioritizationFallbackReason],
  );

  // ── WHY THIS MATTERS lookup ─────────────────────────────────────────────────
  const productById = useMemo(() => {
    const m = new Map<string, DataProduct>();
    for (const p of props.rawDataProducts) m.set(p.product_id, p);
    return m;
  }, [props.rawDataProducts]);

  const anomalyById = useMemo(() => {
    const m = new Map<string, AnomalyEvent>();
    for (const a of props.anomalies) m.set(a.anomaly_id, a);
    return m;
  }, [props.anomalies]);

  // Auto-select first ranked product when AI result arrives
  useEffect(() => {
    if (hasResult && props.aiPrioritization && !selectedRpId) {
      const first = props.aiPrioritization.ranked_products.find((r) => r.priority === 1)
        ?? props.aiPrioritization.ranked_products[0];
      if (first) setSelectedRpId(first.product_id);
    }
  }, [hasResult, props.aiPrioritization, selectedRpId]);

  const selectedRp: RankedProduct | null = useMemo(() => {
    if (!selectedRpId || !props.aiPrioritization) return null;
    return props.aiPrioritization.ranked_products.find((r) => r.product_id === selectedRpId) ?? null;
  }, [selectedRpId, props.aiPrioritization]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
      {/* AI Copilot status card — always visible */}
      <div style={{
        ...CARD,
        background: isAnalyzing ? 'rgba(76,141,255,0.06)' : isReady ? 'rgba(52,211,153,0.04)' : isError ? 'rgba(248,113,113,0.04)' : isStale ? 'rgba(245,158,11,0.04)' : 'rgba(8,12,22,0.85)',
        borderColor: isAnalyzing ? 'rgba(76,141,255,0.22)' : isReady ? 'rgba(52,211,153,0.18)' : isError ? 'rgba(248,113,113,0.22)' : isStale ? 'rgba(245,158,11,0.22)' : 'rgba(46,58,79,0.7)',
        marginBottom: 6,
        flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: hasResult ? 8 : 0 }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', display: 'inline-block', background: statusColor, flexShrink: 0 }} />
          <span style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', color: statusColor }}>
            {hasResult ? triageInfo.title : `AI COPILOT · ${statusLabel}`}
          </span>
          {hasResult && triageInfo.subtitle && (
            <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#f59e0b', background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.25)', borderRadius: 2, padding: '1px 5px' }}>
              {triageInfo.subtitle}
            </span>
          )}
          {props.aiProvider && (isReady || isStale) && (
            <span style={{ marginLeft: 'auto', fontFamily: '"IBM Plex Mono"', fontSize: 9, color: triageInfo.isLocal ? '#f59e0b' : 'rgba(110,168,255,0.6)', flexShrink: 0 }}>
              {props.aiProvider}
            </span>
          )}
        </div>

        {/* Compact summary row when ready */}
        {hasResult && props.aiPrioritization && (
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: 'rgba(147,160,180,0.4)', letterSpacing: '0.08em' }}>TOTAL QUEUED</div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 13, fontWeight: 700, color: '#f59e0b' }}>
                {funnelData.totalQueued.toLocaleString()}
              </div>
            </div>
            <div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: 'rgba(147,160,180,0.4)', letterSpacing: '0.08em' }}>CANDIDATES</div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 13, fontWeight: 700, color: '#6EA8FF' }}>
                {funnelData.semanticCandidates}
              </div>
            </div>
            <div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: 'rgba(147,160,180,0.4)', letterSpacing: '0.08em' }}>URGENT</div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 13, fontWeight: 700, color: '#f87171' }}>
                {funnelData.urgentCount}
              </div>
            </div>
            <div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 8, color: 'rgba(147,160,180,0.4)', letterSpacing: '0.08em' }}>FIT CONTACT</div>
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 13, fontWeight: 700, color: '#34d399' }}>
                {funnelData.projectedFit ?? '—'}
              </div>
            </div>
            {isStale && (
              <span style={{ alignSelf: 'center', fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#f59e0b', background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.25)', borderRadius: 3, padding: '2px 6px' }}>
                STALE
              </span>
            )}
          </div>
        )}

        {/* Context summary — standby/stale/not-AI mode */}
        {(isStandby || isStale || notInAiMode) && !hasResult && (
          <div style={{ marginTop: hasResult ? 0 : 4 }}>
            <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: 'rgba(147,160,180,0.4)', letterSpacing: '0.08em', marginBottom: 5 }}>MISSION CONTEXT</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              {dp > 0 && (
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, color: 'rgba(147,160,180,0.7)' }}>Data products</span>
                  <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 12, fontWeight: 700, color: '#f59e0b' }}>{dp}</span>
                </div>
              )}
              {anomCount > 0 && (
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, color: 'rgba(147,160,180,0.7)' }}>Active anomalies</span>
                  <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 12, fontWeight: 700, color: '#f87171' }}>{anomCount}</span>
                </div>
              )}
              {ms && (
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, color: 'rgba(147,160,180,0.7)' }}>Comm window</span>
                  <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 12, color: '#34d399' }}>{ms.comm_window_remaining_s.toFixed(0)} s</span>
                </div>
              )}
              {ls && (
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, color: 'rgba(147,160,180,0.7)' }}>Link status</span>
                  <span style={{ fontFamily: '"IBM Plex Mono"', fontSize: 11, color: ls.link_stability > 0.7 ? '#34d399' : '#f59e0b' }}>
                    {ls.link_stability > 0.85 ? 'Stable' : ls.link_stability > 0.6 ? 'Degraded' : 'Unstable'}
                  </span>
                </div>
              )}
            </div>
            {isStandby && !isAnalyzing && (
              <div style={{ marginTop: 7, fontFamily: '"IBM Plex Sans"', fontSize: 11, color: 'rgba(147,160,180,0.45)', lineHeight: 1.5 }}>
                AI has not made any recommendation. No analysis has been requested.
              </div>
            )}
          </div>
        )}

        {/* Analyzing progress */}
        {isAnalyzing && (
          <div style={{ marginTop: 6 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {[
                { done: true, label: 'Mission state loaded' },
                { done: true, label: `${dp} data products ready` },
                { done: true, label: `${anomCount} anomalies identified` },
                { done: true, label: 'Communication constraints evaluated' },
                { done: false, label: `Requesting AI analysis (${props.aiProvider ?? 'provider'})…` },
              ].map(({ done, label }) => (
                <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 6, fontFamily: '"IBM Plex Sans"', fontSize: 11, color: done ? 'rgba(147,160,180,0.75)' : '#6EA8FF' }}>
                  <span style={{ flexShrink: 0 }}>{done ? '✓' : '●'}</span>
                  {label}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Error state */}
        {isError && (
          <div style={{ background: 'rgba(248,113,113,0.07)', border: '1px solid rgba(248,113,113,0.25)', borderRadius: 5, padding: '8px 10px', marginTop: 8 }}>
            <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, fontWeight: 700, color: '#f87171', marginBottom: 4 }}>⚠ ANALYSIS FAILED</div>
            {props.aiProvider && (
              <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: 'rgba(147,160,180,0.7)', marginBottom: 3 }}>Provider: {props.aiProvider}</div>
            )}
            {props.aiError && (
              <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 10, color: 'rgba(248,113,113,0.7)', wordBreak: 'break-all', lineHeight: 1.4 }}>
                {props.aiError.slice(0, 200)}
              </div>
            )}
            <div style={{ marginTop: 6, fontFamily: '"IBM Plex Sans"', fontSize: 11, color: 'rgba(147,160,180,0.5)' }}>
              Mission operations remain available. Use Manual mode if needed.
            </div>
          </div>
        )}

        {/* Rejected state */}
        {props.aiRecommendationRejected && (
          <div style={{ background: 'rgba(248,113,113,0.06)', border: '1px solid rgba(248,113,113,0.22)', borderRadius: 5, padding: '8px 10px', marginTop: 8 }}>
            <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, fontWeight: 700, color: '#f87171', marginBottom: 3 }}>AI RECOMMENDATION REJECTED</div>
            <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: 'rgba(147,160,180,0.6)', lineHeight: 1.5, marginBottom: 8 }}>
              No transmission was initiated.
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              <button
                onClick={() => props.onSelectDecisionMode('manual')}
                style={{ flex: 1, padding: '6px 0', fontSize: 11, fontFamily: '"IBM Plex Sans"', fontWeight: 600, cursor: 'pointer', background: 'rgba(52,211,153,0.08)', color: '#34d399', border: '1px solid rgba(52,211,153,0.28)', borderRadius: 5 }}
              >
                Return to Manual Planning
              </button>
              <button
                onClick={() => { props.onRunAiAnalysis(); }}
                style={{ flex: 1, padding: '6px 0', fontSize: 11, fontFamily: '"IBM Plex Sans"', fontWeight: 600, cursor: 'pointer', background: 'rgba(76,141,255,0.08)', color: '#6EA8FF', border: '1px solid rgba(76,141,255,0.28)', borderRadius: 5 }}
              >
                Re-run Analysis
              </button>
            </div>
          </div>
        )}

        {/* Action button */}
        {(isStandby || isStale || isError) && !props.aiRecommendationRejected && (
          <button
            onClick={() => {
              if (props.decisionMode !== 'ai') props.onSelectDecisionMode('ai');
              props.onRunAiAnalysis();
            }}
            disabled={isAnalyzing}
            style={{
              width: '100%', padding: '8px 0', marginTop: 8,
              background: 'rgba(76,141,255,0.12)',
              color: '#6EA8FF',
              border: '1px solid rgba(76,141,255,0.35)',
              borderRadius: 6, cursor: 'pointer',
              fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, fontWeight: 600,
              transition: 'background 0.15s',
            }}
          >
            {isStale ? 'Re-analyze Mission' : isError ? 'Retry AI Analysis' : 'Analyze Mission with AI'}
          </button>
        )}
        {isAnalyzing && (
          <button disabled style={{
            width: '100%', padding: '8px 0', marginTop: 8,
            background: 'rgba(76,141,255,0.06)',
            color: 'rgba(110,168,255,0.5)',
            border: '1px solid rgba(76,141,255,0.15)',
            borderRadius: 6,
            fontFamily: '"IBM Plex Sans"', fontSize: 12, fontWeight: 600,
            cursor: 'not-allowed',
          }}>
            Analyzing…
          </button>
        )}
      </div>

      {/* Tabbed AI result workspace — shown when results are ready */}
      {hasResult && (
        <div style={{
          display: 'flex', flexDirection: 'column',
          border: '1px solid rgba(46,58,79,0.7)', borderRadius: 8,
          marginTop: 4,
        }}>
          {/* Tab bar */}
          <TabBar<AiTab>
            tabs={[
              { id: 'prioritization', label: 'Prioritization', badge: rankedCount ?? undefined },
              { id: 'reasoning', label: 'Reasoning' },
              { id: 'decision', label: 'Decision' },
            ]}
            active={activeTab}
            onSelect={setActiveTab}
          />

          {/* Tab content — natural height, Main Control scrolls */}
          <div style={{ padding: '10px', overflowX: 'hidden' }}>
            {activeTab === 'prioritization' && (
              <>
                {/* AI funnel */}
                <AiFunnel
                  totalQueued={funnelData.totalQueued}
                  semanticCandidates={funnelData.semanticCandidates}
                  urgentCount={funnelData.urgentCount}
                  projectedFit={funnelData.projectedFit}
                />

                {/* WHY THIS MATTERS for selected product */}
                {selectedRp && (
                  <WhyThisMatters
                    rp={selectedRp}
                    dataProduct={productById.get(selectedRp.product_id)}
                    anomaly={selectedRp.anomaly_ids.length > 0 ? anomalyById.get(selectedRp.anomaly_ids[0]) : undefined}
                  />
                )}

                {/* Ranked product selector */}
                {props.aiPrioritization && props.aiPrioritization.ranked_products.length > 0 && (
                  <div style={{ marginBottom: 10 }}>
                    <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: 'rgba(147,160,180,0.45)', letterSpacing: '0.08em', marginBottom: 5 }}>
                      SELECT PRODUCT TO VIEW EVIDENCE
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 1, maxHeight: 200, overflowY: 'auto' }}>
                      {props.aiPrioritization.ranked_products
                        .slice()
                        .sort((a, b) => a.priority - b.priority)
                        .map((rp) => {
                          const isSelected = rp.product_id === selectedRpId;
                          return (
                            <button
                              key={rp.product_id}
                              onClick={() => setSelectedRpId(rp.product_id)}
                              style={{
                                display: 'flex', alignItems: 'center', gap: 8,
                                padding: '5px 8px', textAlign: 'left', cursor: 'pointer',
                                background: isSelected ? 'rgba(76,141,255,0.12)' : 'transparent',
                                border: `1px solid ${isSelected ? 'rgba(76,141,255,0.35)' : 'rgba(46,58,79,0.5)'}`,
                                borderRadius: 4,
                                fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
                              }}
                            >
                              <span style={{ fontSize: 9, color: 'rgba(147,160,180,0.45)', minWidth: 20, textAlign: 'right' }}>
                                #{rp.priority}
                              </span>
                              <span style={{ fontSize: 11, color: isSelected ? '#6EA8FF' : 'rgba(147,160,180,0.8)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {rp.product_id}
                              </span>
                              {rp.anomaly_ids.length > 0 && (
                                <span style={{ color: '#f87171', fontSize: 9, flexShrink: 0 }}>⚠</span>
                              )}
                            </button>
                          );
                        })}
                    </div>
                  </div>
                )}

                {/* Full AI prioritization detail */}
                <AIDecisionPanel
                  prioritization={props.aiPrioritization}
                  providerName={props.aiActualProvider ?? props.aiProvider}
                  requestedProviderName={props.aiRequestedProvider}
                  candidateCount={props.aiCandidateCount}
                  totalProducts={props.dataProductsCount > 0 ? props.dataProductsCount : null}
                  prioritizationFallbackReason={props.aiPrioritizationFallbackReason}
                  recommendationFallbackReason={props.aiRecommendationFallbackReason}
                />
              </>
            )}
            {activeTab === 'reasoning' && (
              <RecommendationPanel
                recommendation={props.recommendation}
                providerName={props.aiActualProvider ?? props.aiProvider}
                requestedProviderName={props.aiRequestedProvider}
                recommendationFallbackReason={props.aiRecommendationFallbackReason}
                evaluation={props.recEval}
                riskWeights={props.riskWeights}
              />
            )}
            {activeTab === 'decision' && (
              <>
                {/* Human decision panel */}
                <AiHumanDecisionPanel props={props} />

                {/* Full decision chain */}
                <MissionDecisionPanel
                  prioritization={props.aiPrioritization}
                  recommendation={props.recommendation}
                  allPlans={props.allPlans}
                  recEval={props.recEval}
                  linkState={props.linkState}
                  providerName={props.aiActualProvider ?? props.aiProvider}
                  prioritizationError={props.aiPrioritizationError}
                  candidateCount={props.aiCandidateCount}
                />
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Decision Mode selector ─────────────────────────────────────────────────────

function DecisionModeSelector(props: CommonProps) {
  const dp = props.dataProductsCount;
  const isLegacy = !props.hasDataProducts;

  if (isLegacy) {
    return (
      <div style={{ ...CARD, borderColor: 'rgba(245,158,11,0.28)', background: 'rgba(245,158,11,0.04)' }}>
        <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: '#f59e0b', letterSpacing: '0.1em', marginBottom: 8 }}>LEGACY PACKET SCENARIO</div>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, color: 'rgba(147,160,180,0.8)', lineHeight: 1.55 }}>
          This scenario uses the legacy packet model. AI prioritization and high-volume manual planning are not available.
        </div>
      </div>
    );
  }

  return (
    <>
      <div style={{ ...CARD, borderColor: 'rgba(76,141,255,0.18)', marginBottom: 10 }}>
        <div style={{ fontFamily: '"IBM Plex Mono"', fontSize: 9, color: 'rgba(76,141,255,0.7)', letterSpacing: '0.1em', marginBottom: 10 }}>
          DECISION WORKFLOW
        </div>
        <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, color: 'rgba(147,160,180,0.8)', lineHeight: 1.55, marginBottom: 14 }}>
          <strong style={{ color: '#f59e0b' }}>{dp} data products</strong> are awaiting downlink.
          Communication resources are limited. Choose how to build the transmission plan.
        </div>

        <div style={{
          border: `1px solid ${props.decisionMode === 'manual' ? 'rgba(52,211,153,0.35)' : 'rgba(46,58,79,0.7)'}`,
          borderRadius: 8, padding: '12px 14px', marginBottom: 8,
          background: props.decisionMode === 'manual' ? 'rgba(52,211,153,0.06)' : 'rgba(255,255,255,0.02)',
        }}>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, fontWeight: 600, color: props.decisionMode === 'manual' ? '#34d399' : '#e2e8f4', marginBottom: 6 }}>
            MANUAL DECISION
          </div>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: 'rgba(147,160,180,0.7)', lineHeight: 1.5, marginBottom: 10 }}>
            Review and prioritize mission data yourself. Browse all {dp} products, apply filters, select what to transmit.
          </div>
          <button
            onClick={() => props.onSelectDecisionMode('manual')}
            style={{
              width: '100%', padding: '7px 0',
              background: props.decisionMode === 'manual' ? 'rgba(52,211,153,0.15)' : 'rgba(52,211,153,0.08)',
              color: '#34d399', border: '1px solid rgba(52,211,153,0.3)',
              borderRadius: 5, cursor: 'pointer', fontFamily: '"IBM Plex Sans"', fontSize: 12, fontWeight: 600,
            }}
          >
            {props.decisionMode === 'manual' ? '✓ Manual Mode Active' : 'Start Manual Planning'}
          </button>
        </div>

        <div style={{
          border: `1px solid ${props.decisionMode === 'ai' ? 'rgba(76,141,255,0.35)' : 'rgba(46,58,79,0.7)'}`,
          borderRadius: 8, padding: '12px 14px',
          background: props.decisionMode === 'ai' ? 'rgba(76,141,255,0.06)' : 'rgba(255,255,255,0.02)',
        }}>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, fontWeight: 600, color: props.decisionMode === 'ai' ? '#6EA8FF' : '#e2e8f4', marginBottom: 6 }}>
            AI ASSISTED
          </div>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: 'rgba(147,160,180,0.7)', lineHeight: 1.5, marginBottom: 10 }}>
            Ask the AI Copilot to analyze the mission context, anomalies, deadlines, and constraints — then recommend a prioritized transmission plan.
          </div>
          <button
            onClick={() => props.onSelectDecisionMode('ai')}
            style={{
              width: '100%', padding: '7px 0',
              background: props.decisionMode === 'ai' ? 'rgba(76,141,255,0.15)' : 'rgba(76,141,255,0.08)',
              color: '#6EA8FF', border: '1px solid rgba(76,141,255,0.3)',
              borderRadius: 5, cursor: 'pointer', fontFamily: '"IBM Plex Sans"', fontSize: 12, fontWeight: 600,
            }}
          >
            {props.decisionMode === 'ai' ? '✓ AI Mode Active' : 'Use AI Assistant'}
          </button>
        </div>
      </div>

      {props.decisionMode === 'ai' && <AiSection {...props} />}

      {props.decisionMode === 'manual' && (
        <div style={{ ...CARD, borderColor: 'rgba(52,211,153,0.2)' }}>
          <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 12, color: 'rgba(147,160,180,0.8)', lineHeight: 1.5 }}>
            Manual mode active. Navigate to the <strong style={{ color: '#34d399' }}>Data</strong> section to browse and select data products.
          </div>
          {props.manualSelectedIds.size > 0 && (
            <div style={{ marginTop: 8, fontFamily: '"IBM Plex Mono"', fontSize: 11, color: '#34d399' }}>
              {props.manualSelectedIds.size} products selected
            </div>
          )}
        </div>
      )}
    </>
  );
}

// ── Transmission section ──────────────────────────────────────────────────────

function TransmissionSection(props: CommonProps) {
  const activeTxPlan = props.decisionMode === 'manual' && props.manualPlan
    ? props.manualPlan
    : (props.recPlan ?? props.activePlan);
  const activeTxEval = props.decisionMode === 'manual'
    ? null
    : (props.recEval ?? props.activeEval);

  // Show choreography sequence panel when active (survives navigation — executionId persists)
  if (props.choreographyActive && props.executionId) {
    return (
      <TransmissionSequencePanel
        initialPhase="plan_uplink"
        pendingPlan={props.pendingExecutionPlan}
        playbackConfig={props.experienceManifest?.playback ?? null}
        propagationDelayS={props.propagationDelayS}
        availableCapacityBits={props.availableCapacityBits}
        executionId={props.executionId}
        playbackStartedAtMs={props.playbackStartedAtMs}
        onSetPlaybackStarted={props.onSetPlaybackStarted}
        onExecuteApproval={props.onExecuteApproval}
        onComplete={props.onChoreographyComplete}
        onError={props.onChoreographyError}
      />
    );
  }

  // AI Assisted mode: show "AWAITING AUTHORIZATION" until operator approves in Decision
  const isAiMode = props.decisionMode === 'ai';
  const isTransmissionComplete = props.approvalPhase === 'complete';

  return (
    <>
      <ResizableSection title="Transmission Summary" icon="↗" accent="#4C8DFF">
        <div style={{ minWidth: 0 }}>
          <TransmissionSummaryPanel
            plan={activeTxPlan}
            evaluation={activeTxEval}
            availableCapacityBits={props.availableCapacityBits}
          />
        </div>
      </ResizableSection>

      {/* AI mode: single authorization point gate */}
      {isAiMode && !isTransmissionComplete && (
        <ResizableSection title="Authorization Required" icon="◉" accent="#4C8DFF">
          <div style={{
            background: 'rgba(8,12,22,0.9)',
            border: '1px solid rgba(76,141,255,0.18)',
            borderRadius: 6, padding: '14px 16px',
          }}>
            <div style={{ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 9, color: 'rgba(147,160,180,0.55)', letterSpacing: '0.1em', marginBottom: 10 }}>
              AWAITING OPERATOR AUTHORIZATION
            </div>
            <div style={{ fontFamily: '"IBM Plex Sans", system-ui', fontSize: 12, color: 'rgba(147,160,180,0.8)', lineHeight: 1.6, marginBottom: 14 }}>
              Review the final recommendation in <strong style={{ color: '#6EA8FF' }}>AI Copilot → Decision</strong>.
            </div>
            <div style={{ fontFamily: '"IBM Plex Sans"', fontSize: 11, color: 'rgba(147,160,180,0.55)', marginBottom: 12, lineHeight: 1.5 }}>
              Use <strong>✓ APPROVE TRANSMISSION</strong> in the Decision tab to authorize a single authoritative execution.
              Once approved, this Transmission panel will show execution status and playback.
            </div>
            <button
              onClick={() => props.onSelectDecisionMode('ai')}
              style={{
                width: '100%', padding: '8px 0', fontSize: 12, fontWeight: 600,
                fontFamily: '"IBM Plex Sans", system-ui', cursor: 'pointer',
                background: 'rgba(76,141,255,0.10)', color: '#6EA8FF',
                border: '1px solid rgba(76,141,255,0.30)', borderRadius: 6,
              }}
            >
              GO TO DECISION
            </button>
          </div>
        </ResizableSection>
      )}

      {/* Manual mode: keep Approval bar with EVALUATE SELECTION / TRANSMIT SELECTED */}
      {!isAiMode && (
      <ResizableSection title="Approval" icon="◉" accent="#4C8DFF">
        <div style={{ minWidth: 0 }}>
          <ApprovalBar
            recommendedPlanId={props.recommendation ? props.recommendation.recommended_plan_id : null}
            recommendedPlan={
              props.decisionMode === 'manual' && props.manualPlan
                ? props.manualPlan
                : props.recPlan
            }
            baselinePlan={props.queue}
            approvalPhase={props.approvalPhase}
            onApproved={props.onApproved}
            onTransmitting={props.onTransmitting}
            onApprovalError={props.onApprovalError}
            decisionMode={props.decisionMode}
            manualOrder={props.manualOrder}
            rawDataProducts={props.rawDataProducts}
            manualEvaluation={props.manualAssessment?.evaluation ?? null}
            onManualEvaluate={props.onManualEvaluate}
            onManualTransmit={props.onManualTransmit}
            availableCapacityBits={props.availableCapacityBits}
          />
          {/* Show manual assessment details when available */}
          {props.decisionMode === 'manual' && props.manualAssessment && (
            <div style={{
              marginTop: 8,
              background: props.manualAssessmentStale ? 'rgba(245,158,11,0.05)' : 'rgba(52,211,153,0.04)',
              border: `1px solid ${props.manualAssessmentStale ? 'rgba(245,158,11,0.25)' : 'rgba(52,211,153,0.18)'}`,
              borderRadius: 4, padding: '8px 12px',
              fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 11,
            }}>
              {props.manualAssessmentStale && (
                <div style={{ color: '#f59e0b', marginBottom: 4, fontSize: 10 }}>⚠ STALE — Re-evaluate to update</div>
              )}
              <div style={{ color: 'rgba(147,160,180,0.8)', marginBottom: 4, fontSize: 10 }}>MANUAL PLAN ASSESSMENT</div>
              <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                <div>
                  <div style={{ color: 'rgba(147,160,180,0.55)', fontSize: 8, textTransform: 'uppercase', marginBottom: 2 }}>SELECTED</div>
                  <div style={{ color: '#e2e8f4', fontSize: 12, fontWeight: 700 }}>{props.manualAssessment.capacity_summary.selected_count}</div>
                </div>
                <div>
                  <div style={{ color: 'rgba(147,160,180,0.55)', fontSize: 8, textTransform: 'uppercase', marginBottom: 2 }}>PAYLOAD</div>
                  <div style={{ color: '#e2e8f4', fontSize: 12, fontWeight: 700 }}>{formatBitsAsDataVolume(props.manualAssessment.capacity_summary.selected_bits)}</div>
                </div>
                <div>
                  <div style={{ color: 'rgba(147,160,180,0.55)', fontSize: 8, textTransform: 'uppercase', marginBottom: 2 }}>RISK</div>
                  <div style={{
                    fontSize: 12, fontWeight: 700,
                    color: props.manualAssessment.evaluation.risk_level === 'LOW' ? '#34d399' :
                           props.manualAssessment.evaluation.risk_level === 'MEDIUM' ? '#f59e0b' :
                           props.manualAssessment.evaluation.risk_level === 'HIGH' ? '#fb923c' : '#f87171',
                  }}>
                    {props.manualAssessment.evaluation.risk_level}
                  </div>
                </div>
                <div>
                  <div style={{ color: 'rgba(147,160,180,0.55)', fontSize: 8, textTransform: 'uppercase', marginBottom: 2 }}>DEFERRED</div>
                  <div style={{ color: '#e2e8f4', fontSize: 12, fontWeight: 700 }}>{props.manualAssessment.evaluation.deferred_packets.length}</div>
                </div>
              </div>
            </div>
          )}
          {props.decisionMode === 'manual' && props.manualAssessmentLoading && (
            <div style={{ marginTop: 6, color: 'rgba(147,160,180,0.55)', fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 11 }}>
              Evaluating…
            </div>
          )}
          {props.decisionMode === 'manual' && props.manualAssessmentError && (
            <div style={{ marginTop: 6, color: '#f87171', fontFamily: '"IBM Plex Mono", ui-monospace', fontSize: 11 }}>
              Assessment error: {props.manualAssessmentError}
            </div>
          )}
        </div>
      </ResizableSection>
      )}

      <TransmissionOutcomeBanner
        approvalPhase={props.approvalPhase}
        simulationResult={props.approveResult?.simulation_result ?? null}
        isAiRecommendedPlan={
          props.approveResult?.simulation_result?.plan_id !== undefined &&
          props.approveResult.simulation_result.plan_id !== 'operator-override' &&
          props.approveResult.simulation_result.plan_id !== 'operator-manual'
        }
      />
    </>
  );
}

// ── V3.5 / F5: Log section — Tabbed workspace ────────────────────────────────

type LogTab = 'simulation' | 'narrative' | 'ground' | 'comparison' | 'sessionlog' | 'report';

function LogSection(props: CommonProps) {
  const hasResult = !!props.approveResult;
  const hasManualAssessment = !!props.manualAssessment && !props.manualAssessmentStale;
  const hasAiEval = !!props.recEval && !!props.recPlan;
  const showComparison = hasManualAssessment && hasAiEval;

  const [activeTab, setActiveTab] = useState<LogTab>(hasResult ? 'ground' : 'sessionlog');

  const isAiPlan = props.approveResult
    ? (props.approveResult.simulation_result.plan_id !== undefined &&
       props.approveResult.simulation_result.plan_id !== 'operator-override' &&
       props.approveResult.simulation_result.plan_id !== 'operator-manual')
    : false;

  const tabs: TabItem<LogTab>[] = [
    { id: 'ground', label: 'Reception', badge: hasResult ? '✓' : undefined },
    { id: 'simulation', label: 'Simulation' },
    { id: 'narrative', label: 'Narrative' },
    ...(showComparison ? [{ id: 'comparison' as const, label: 'Manual vs AI' }] : []),
    { id: 'sessionlog', label: 'Session Log', badge: props.sessionEvents.length > 0 ? props.sessionEvents.length : undefined },
    { id: 'report', label: 'Report' },
  ];

  return (
    <div style={{
      display: 'flex', flexDirection: 'column',
      border: '1px solid rgba(46,58,79,0.7)', borderRadius: 8,
    }}>
      {/* Tab bar */}
      <TabBar<LogTab>
        tabs={tabs}
        active={activeTab}
        onSelect={setActiveTab}
      />

      {/* Tab content — natural height, Main Control scrolls */}
      <div style={{ padding: '10px', overflowX: 'hidden' }}>
        {activeTab === 'ground' && (
          <>
            {hasResult ? (
              <GroundReceptionPanel
                approveResult={props.approveResult!}
                anomalies={props.anomalies}
                groundInformationObjectives={props.experienceManifest?.ground_information_objectives ?? null}
                groundStationName={props.experienceManifest?.display.ground_station_name}
              />
            ) : (
              <div style={{ color: 'rgba(147,160,180,0.4)', fontSize: 12, padding: '20px 0', textAlign: 'center' }}>
                No ground reception data yet. Complete a transmission to see the ground station reception.
              </div>
            )}
          </>
        )}
        {activeTab === 'simulation' && (
          <>
            {hasResult ? (
              <SimulationPanel
                approveResult={props.approveResult!}
                propagationDelayS={props.propagationDelayS}
              />
            ) : (
              <div style={{ color: 'rgba(147,160,180,0.4)', fontSize: 12, padding: '20px 0', textAlign: 'center' }}>
                No simulation data yet. Approve a transmission plan to run the simulation.
              </div>
            )}
          </>
        )}
        {activeTab === 'narrative' && (
          <>
            {hasResult ? (
              <TransmissionNarrativePanel
                prioritization={props.aiPrioritization}
                simulationResult={props.approveResult!.simulation_result}
                anomalies={props.anomalies}
                isAiRecommendedPlan={isAiPlan}
              />
            ) : (
              <div style={{ color: 'rgba(147,160,180,0.4)', fontSize: 12, padding: '20px 0', textAlign: 'center' }}>
                No transmission narrative yet. Complete a transmission to generate the mission narrative.
              </div>
            )}
          </>
        )}
        {activeTab === 'comparison' && showComparison && (
          <ManualVsAiPanel
            manualAssessment={props.manualAssessment!}
            aiEval={props.recEval!}
            aiPlanPayloadBits={(props.recPlan?.packets ?? []).reduce((s, p) => s + p.size_bits, 0)}
            aiPlanPacketCount={props.recPlan?.packets.length ?? 0}
            availableCapacityBits={props.availableCapacityBits}
          />
        )}
        {activeTab === 'sessionlog' && (
          <SessionLogPanel events={props.sessionEvents} />
        )}
        {activeTab === 'report' && (
          <MissionReportPanel
            approvalPhase={props.approvalPhase}
            missionState={props.missionState}
            recommendation={props.recommendation}
            aiPrioritization={props.aiPrioritization}
            aiProvider={props.aiProvider}
            simulationResult={props.approveResult?.simulation_result ?? null}
            anomalies={props.anomalies}
            distanceKm={props.distanceKm}
            propagationDelayS={props.propagationDelayS}
            roundTripTimeS={props.roundTripTimeS}
          />
        )}
      </div>
    </div>
  );
}

// ── Section headings ──────────────────────────────────────────────────────────

const SECTION_HEADINGS: Record<NavSection, { icon: string; title: string }> = {
  mission:      { icon: '◉', title: 'Mission Control' },
  spacecraft:   { icon: '⬡', title: 'Spacecraft' },
  comms:        { icon: '⌾', title: 'Link Health' },
  data:         { icon: '▦', title: 'Data Products' },
  ai:           { icon: '◈', title: 'AI Copilot' },
  transmission: { icon: '↗', title: 'Transmission' },
  log:          { icon: '≡', title: 'Mission Log' },
  config:       { icon: '⚙', title: 'Configuration' },
};

// ── V3.5: Workspace mode control buttons ──────────────────────────────────────

function WorkspaceModeControls({
  mode,
  onSet,
}: {
  mode: WorkspaceMode;
  onSet: (m: WorkspaceMode) => void;
}) {
  const btnBase: React.CSSProperties = {
    background: 'transparent',
    border: '1px solid rgba(46,58,79,0.7)',
    borderRadius: 4,
    color: 'rgba(147,160,180,0.55)',
    cursor: 'pointer',
    fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
    fontSize: 11,
    padding: '3px 6px',
    lineHeight: 1,
    transition: 'color 0.15s, border-color 0.15s, background 0.15s',
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
  };
  const btnActive: React.CSSProperties = {
    ...btnBase,
    color: '#4C8DFF',
    border: '1px solid rgba(76,141,255,0.4)',
    background: 'rgba(76,141,255,0.08)',
  };

  if (mode === 'focus') {
    return (
      <button
        style={{ ...btnBase, color: '#f87171', border: '1px solid rgba(248,113,113,0.35)', background: 'rgba(248,113,113,0.06)' }}
        onClick={() => onSet('normal')}
        title="Exit focus mode (Esc)"
        aria-label="Exit focus mode"
      >
        ↩ Exit Focus
      </button>
    );
  }

  return (
    <div style={{ display: 'flex', gap: 4 }}>
      {mode === 'expanded' && (
        <button
          style={btnActive}
          onClick={() => onSet('normal')}
          title="Return to normal mode"
          aria-label="Return to normal workspace"
        >
          ↔ Normal
        </button>
      )}
      {mode === 'normal' && (
        <button
          style={btnBase}
          onClick={() => onSet('expanded')}
          title="Expand workspace"
          aria-label="Expand workspace"
        >
          ⇔
        </button>
      )}
      <button
        style={btnBase}
        onClick={() => onSet('focus')}
        title="Focus workspace — full panel (Ctrl+Shift+F)"
        aria-label="Enter focus workspace"
      >
        ⛶
      </button>
    </div>
  );
}

// ── RightPanelProps ───────────────────────────────────────────────────────────

interface RightPanelProps extends CommonProps {
  section: NavSection;
  viewSettings: ViewSettings;
  onUpdateSetting: <K extends keyof ViewSettings>(key: K, value: ViewSettings[K]) => void;
  onResetSettings: () => void;
  onResetPanelWidth: () => void;
  panelWidth: number;
  panelDefaultWidth: number;
}

// ── RightPanel ────────────────────────────────────────────────────────────────

export function RightPanel({
  section,
  viewSettings,
  onUpdateSetting,
  onResetSettings,
  onResetPanelWidth,
  panelWidth,
  panelDefaultWidth,
  workspaceMode,
  onSetWorkspaceMode,
  ...props
}: RightPanelProps) {
  const heading = SECTION_HEADINGS[section];
  const isFocus = workspaceMode === 'focus';
  const isExpanded = workspaceMode === 'expanded';

  // Compute effective panel width from workspace mode
  const effectiveWidth = isFocus ? undefined : isExpanded ? 'clamp(650px, 58vw, 1100px)' : panelWidth;

  // V3.5.2: purge obsolete per-section height keys written by the old ResizableSection
  useEffect(() => {
    try {
      const stale = Object.keys(localStorage).filter((k) => k.startsWith('GCSI_SEC_H_'));
      stale.forEach((k) => localStorage.removeItem(k));
    } catch { /* ignore */ }
  }, []);

  // AI lifecycle badge for section header
  const aiStatusBadge = section === 'ai' && (
    <span style={{
      marginLeft: 8, fontSize: 9, fontWeight: 700,
      background: props.aiLifecycle === 'ready' ? 'rgba(52,211,153,0.10)' :
                  props.aiLifecycle === 'analyzing' ? 'rgba(76,141,255,0.10)' :
                  props.aiLifecycle === 'error' ? 'rgba(248,113,113,0.10)' :
                  props.aiLifecycle === 'stale' ? 'rgba(245,158,11,0.10)' : 'transparent',
      color: props.aiLifecycle === 'ready' ? '#34d399' :
             props.aiLifecycle === 'analyzing' ? '#6EA8FF' :
             props.aiLifecycle === 'error' ? '#f87171' :
             props.aiLifecycle === 'stale' ? '#f59e0b' : 'transparent',
      border: `1px solid ${props.aiLifecycle === 'ready' ? 'rgba(52,211,153,0.25)' :
              props.aiLifecycle === 'analyzing' ? 'rgba(76,141,255,0.25)' :
              props.aiLifecycle === 'error' ? 'rgba(248,113,113,0.25)' :
              props.aiLifecycle === 'stale' ? 'rgba(245,158,11,0.25)' : 'transparent'}`,
      borderRadius: 2, padding: '1px 6px',
      fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
      letterSpacing: '0.05em',
    }}>
      {props.aiLifecycle.toUpperCase()}
    </span>
  );

  const dataBadge = section === 'data' && props.dataProductsCount > 0 && (
    <span style={{
      marginLeft: 8, fontSize: 9, fontWeight: 700,
      background: 'rgba(245,158,11,0.08)',
      color: '#f59e0b',
      border: '1px solid rgba(245,158,11,0.22)',
      borderRadius: 2, padding: '1px 6px',
      fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
    }}>
      {props.dataProductsCount}
    </span>
  );

  // Focus mode indicator
  const focusBadge = isFocus && (
    <span style={{
      marginLeft: 8, fontSize: 9, fontWeight: 700,
      background: 'rgba(248,113,113,0.08)',
      color: '#f87171',
      border: '1px solid rgba(248,113,113,0.25)',
      borderRadius: 2, padding: '1px 6px',
      fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
      letterSpacing: '0.05em',
    }}>
      FOCUS
    </span>
  );

  // Expanded mode indicator
  const expandedBadge = isExpanded && (
    <span style={{
      marginLeft: 8, fontSize: 9, fontWeight: 700,
      background: 'rgba(76,141,255,0.08)',
      color: '#4C8DFF',
      border: '1px solid rgba(76,141,255,0.25)',
      borderRadius: 2, padding: '1px 6px',
      fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
      letterSpacing: '0.05em',
    }}>
      EXPANDED
    </span>
  );

  return (
    <div
      className="workspace-right-panel"
      style={{
        ...(isFocus ? { flex: 1 } : { width: effectiveWidth, flexShrink: 0 }),
        minWidth: isFocus ? 0 : 340,
        maxWidth: isFocus ? undefined : isExpanded ? 1100 : 680,
        background: '#0B0F18',
        borderLeft: '1px solid rgba(46,58,79,0.8)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      {/* Panel header — sticky */}
      <div style={{
        padding: '11px 16px 10px',
        borderBottom: '1px solid rgba(46,58,79,0.7)',
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        gap: 9,
        background: 'rgba(8,12,22,0.80)',
      }}>
        <span style={{ fontSize: 12, color: 'rgba(76,141,255,0.55)', lineHeight: 1 }}>{heading.icon}</span>
        <span style={{
          fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
          fontSize: 12, fontWeight: 600,
          color: '#d4dcea',
          letterSpacing: '0.01em',
          flex: 1,
          minWidth: 0,
        }}>
          {heading.title}
          {aiStatusBadge}
          {dataBadge}
          {focusBadge}
          {expandedBadge}
        </span>
        {/* V3.5: Workspace mode controls */}
        <WorkspaceModeControls mode={workspaceMode} onSet={onSetWorkspaceMode} />
      </div>

      {/* Primary vertical scroll container — single scrollbar for all content */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        overflowX: 'hidden',
        padding: '10px',
        minWidth: 0,
        minHeight: 0,
      }}>
        {section === 'mission'      && <MissionSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />}
        {section === 'spacecraft'   && <SpacecraftSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />}
        {section === 'comms'        && <CommsSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />}
        {section === 'data'         && <DataSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />}
        {section === 'ai'           && <AiSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />}
        {section === 'transmission' && (
          <>
            {props.decisionMode === 'unselected' && (
              <DecisionModeSelector {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />
            )}
            {props.decisionMode !== 'unselected' && <TransmissionSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />}
            {props.decisionMode === 'unselected' && <TransmissionSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />}
          </>
        )}
        {section === 'log'          && <LogSection {...props} workspaceMode={workspaceMode} onSetWorkspaceMode={onSetWorkspaceMode} />}
        {section === 'config'       && (
          <ConfigPanel
            settings={viewSettings}
            onUpdate={onUpdateSetting}
            onResetSettings={onResetSettings}
            onResetPanelWidth={onResetPanelWidth}
            panelWidth={panelWidth}
            panelDefaultWidth={panelDefaultWidth}
            availableScenarios={props.availableScenarios}
            activeScenarioPath={props.activeScenarioPath}
            scenarioSwitching={props.scenarioSwitching}
            onSwitchScenario={props.onSwitchScenario}
          />
        )}
      </div>
    </div>
  );
}
