/**
 * RightPanel — contextual main control panel (right side).
 *
 * V3.3:
 * - ResizableSection used throughout for consistent resize behavior
 * - Table overflow fixed (horizontal scroll inside containers)
 * - Gray + blue palette
 * - Config view wired to ConfigPanel
 * - min-width:0 applied consistently to prevent flex blowout
 */
import type { NavSection } from './NavigationSidebar';
import type {
  AIRecommendation,
  AnomalyEvent,
  ApproveResponse,
  CandidatePlan,
  CandidatePrioritization,
  EvaluationResult,
  LinkState,
  MissionState,
  WhatIfEvalResponse,
} from '../types/domain';
import type { ApprovalPhase } from './ApprovalBar';
import type { ViewSettings } from '../hooks/useViewSettings';

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
import { ResizableSection } from './ResizableSection';
import { ConfigPanel } from './ConfigPanel';

// ── Shared style tokens (V3.3 gray + blue) ──────────────────────────────────

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

/** Wraps a table in a horizontally scrollable container so columns are never clipped */
function TableScroll({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ overflowX: 'auto', overflowY: 'visible', minWidth: 0 }}>
      <div style={{ minWidth: 'max-content' }}>
        {children}
      </div>
    </div>
  );
}

// ── Section panels ────────────────────────────────────────────────────────────

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
  aiPrioritization: CandidatePrioritization | null;
  aiCandidateCount: number | null;
  aiPrioritizationError: string | null;
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
}

// ── Stat tile grid ────────────────────────────────────────────────────────────

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

// ── Mission section ───────────────────────────────────────────────────────────

function MissionSection(props: CommonProps) {
  const ms = props.missionState;
  const ls = props.linkState;

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

      <ResizableSection title="Mission State" icon="◉" accent="#4C8DFF" storageKey="mission-state" defaultHeight={220}>
        {ms ? (
          <TableScroll>
            <MissionStatePanel missionState={ms} />
          </TableScroll>
        ) : (
          <div style={{ color: 'rgba(147,160,180,0.5)', fontSize: 12 }}>No mission data</div>
        )}
      </ResizableSection>

      {props.linkState && (
        <ResizableSection title="Comm Budget" icon="⌾" accent="#4C8DFF" storageKey="comm-budget" defaultHeight={120}>
          <CommBudgetBar
            availableCapacityBits={props.availableCapacityBits}
            queuedDataBits={props.queuedDataBits}
            dataProductsCount={props.dataProductsCount}
            remainingWindowS={props.linkState.remaining_window_s}
          />
        </ResizableSection>
      )}

      {props.anomalies.length > 0 && (
        <ResizableSection title="Anomalies" icon="⚠" accent="#f87171" storageKey="anomalies" defaultHeight={160}>
          {props.anomalies.map((a) => (
            <div key={a.anomaly_id} style={{
              display: 'flex', gap: 10, alignItems: 'flex-start',
              padding: '7px 0', borderBottom: '1px solid rgba(46,58,79,0.5)',
              minWidth: 0,
            }}>
              <span style={{ color: '#f87171', flexShrink: 0, fontSize: 11, marginTop: 1 }}>
                {a.severity >= 4 ? '●' : '○'}
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{
                  color: '#e2e8f4', fontWeight: 600, fontSize: 12,
                  fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
                  wordBreak: 'break-all',
                }}>{a.anomaly_id}</div>
                <div style={{ color: 'rgba(147,160,180,0.8)', fontSize: 11, marginTop: 3, lineHeight: 1.45 }}>{a.description}</div>
                <div style={{ color: 'rgba(147,160,180,0.45)', fontSize: 10, marginTop: 2 }}>
                  {a.subsystem} · severity {a.severity}
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
    <ResizableSection title="Spacecraft Geometry" icon="⬡" accent="#4C8DFF" storageKey="spacecraft-geo" defaultHeight={200}>
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
    <ResizableSection title="Link Health" icon="⌾" accent="#4C8DFF" storageKey="link-health" defaultHeight={300}>
      <div style={{ minWidth: 0 }}>
        <LinkHealthPanel
          linkState={props.linkState}
          onWhatIfResult={props.onWhatIfResult}
        />
      </div>
    </ResizableSection>
  );
}

// ── Data Products section ─────────────────────────────────────────────────────

function DataSection(props: CommonProps) {
  return (
    <>
      {props.dataProductsCount > 0 && (
        <div style={{ ...CARD, display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{
            display: 'inline-block', width: 7, height: 7, borderRadius: '50%',
            background: '#f59e0b', flexShrink: 0,
          }} />
          <div style={{
            fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
            fontSize: 12, color: '#f59e0b', fontWeight: 500,
          }}>
            {props.dataProductsCount} products queued
          </div>
        </div>
      )}
      {props.queue && (
        <ResizableSection title="Transmission Queue" icon="▦" accent="#4C8DFF" storageKey="tx-queue" defaultHeight={320}>
          <div style={{ overflowX: 'auto', minWidth: 0 }}>
            <TransmissionQueuePanel plan={props.queue} />
          </div>
        </ResizableSection>
      )}
    </>
  );
}

// ── AI Copilot section ────────────────────────────────────────────────────────

function AiSection(props: CommonProps) {
  const isAnalyzing = props.approvalPhase === 'ai_analyzing';
  const hasResult = props.recommendation !== null || props.aiPrioritization !== null;
  const isStandby = !isAnalyzing && !hasResult;

  return (
    <>
      {/* AI status card */}
      <div style={{
        ...CARD,
        background: isAnalyzing
          ? 'rgba(76,141,255,0.06)'
          : isStandby ? 'rgba(18,24,34,0.6)' : 'rgba(52,211,153,0.04)',
        borderColor: isAnalyzing
          ? 'rgba(76,141,255,0.22)'
          : isStandby ? 'rgba(46,58,79,0.7)' : 'rgba(52,211,153,0.18)',
        marginBottom: 8,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{
            borderRadius: '50%', width: 7, height: 7,
            display: 'inline-block', flexShrink: 0,
            background: isAnalyzing ? '#4C8DFF' : isStandby ? 'rgba(147,160,180,0.3)' : '#34d399',
          }} />
          <span style={{
            fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
            fontSize: 12, fontWeight: 600,
            color: isAnalyzing ? '#6EA8FF' : isStandby ? 'rgba(147,160,180,0.5)' : '#34d399',
            flex: 1, minWidth: 0,
          }}>
            AI Copilot{isAnalyzing ? ' — Analyzing…' : isStandby ? ' — Standby' : ' — Ready'}
          </span>
          {props.aiProvider && (
            <span style={{
              fontFamily: '"IBM Plex Mono", ui-monospace, monospace',
              fontSize: 9, color: 'rgba(110,168,255,0.55)',
              flexShrink: 0,
            }}>
              {props.aiProvider}
            </span>
          )}
        </div>
        {isStandby && (
          <div style={{
            marginTop: 8, fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
            fontSize: 11.5, color: 'rgba(147,160,180,0.55)', lineHeight: 1.55,
          }}>
            AI analysis runs automatically with each mission refresh.
          </div>
        )}
        {isAnalyzing && (
          <div style={{
            marginTop: 8, fontFamily: '"IBM Plex Sans", system-ui, sans-serif',
            fontSize: 11.5, color: 'rgba(110,168,255,0.65)',
          }}>
            Retrieving AI prioritization…
          </div>
        )}
      </div>

      {hasResult && (
        <>
          <ResizableSection title="AI Prioritization" icon="◈" accent="#6EA8FF" storageKey="ai-prioritization" defaultHeight={280}>
            <div style={{ minWidth: 0 }}>
              <AIDecisionPanel
                prioritization={props.aiPrioritization}
                providerName={props.aiProvider}
                candidateCount={props.aiCandidateCount}
                prioritizationError={props.aiPrioritizationError}
              />
            </div>
          </ResizableSection>

          <ResizableSection title="Recommendation" icon="◉" accent="#34d399" storageKey="recommendation" defaultHeight={240}>
            <div style={{ minWidth: 0 }}>
              <RecommendationPanel
                recommendation={props.recommendation}
                providerName={props.aiProvider}
                evaluation={props.recEval}
                riskWeights={props.riskWeights}
              />
            </div>
          </ResizableSection>

          <ResizableSection title="Mission Decision" icon="▶" accent="#34d399" storageKey="mission-decision" defaultHeight={220}>
            <div style={{ minWidth: 0 }}>
              <MissionDecisionPanel
                prioritization={props.aiPrioritization}
                recommendation={props.recommendation}
                allPlans={props.allPlans}
                recEval={props.recEval}
                linkState={props.linkState}
                providerName={props.aiProvider}
                prioritizationError={props.aiPrioritizationError}
                candidateCount={props.aiCandidateCount}
              />
            </div>
          </ResizableSection>
        </>
      )}
    </>
  );
}

// ── Transmission section ──────────────────────────────────────────────────────

function TransmissionSection(props: CommonProps) {
  return (
    <>
      <ResizableSection title="Transmission Summary" icon="↗" accent="#4C8DFF" storageKey="tx-summary" defaultHeight={200}>
        <div style={{ minWidth: 0 }}>
          <TransmissionSummaryPanel
            plan={props.recPlan ?? props.activePlan}
            evaluation={props.recEval ?? props.activeEval}
            availableCapacityBits={props.availableCapacityBits}
          />
        </div>
      </ResizableSection>

      <ResizableSection title="Approval" icon="◉" accent="#4C8DFF" storageKey="approval" defaultHeight={180}>
        <div style={{ minWidth: 0 }}>
          <ApprovalBar
            recommendedPlanId={props.recommendation ? props.recommendation.recommended_plan_id : null}
            recommendedPlan={props.recPlan}
            baselinePlan={props.queue}
            approvalPhase={props.approvalPhase}
            onApproved={props.onApproved}
            onTransmitting={props.onTransmitting}
            onApprovalError={props.onApprovalError}
          />
        </div>
      </ResizableSection>

      <TransmissionOutcomeBanner
        approvalPhase={props.approvalPhase}
        simulationResult={props.approveResult?.simulation_result ?? null}
        isAiRecommendedPlan={
          props.approveResult?.simulation_result?.plan_id !== undefined &&
          props.approveResult.simulation_result.plan_id !== 'operator-override'
        }
      />
    </>
  );
}

// ── Log section ───────────────────────────────────────────────────────────────

function LogSection(props: CommonProps) {
  return (
    <>
      {props.approveResult && (
        <>
          <ResizableSection title="Simulation" icon="⟳" accent="#4C8DFF" storageKey="simulation" defaultHeight={300}>
            <div style={{ minWidth: 0 }}>
              <SimulationPanel
                approveResult={props.approveResult}
                propagationDelayS={props.propagationDelayS}
              />
            </div>
          </ResizableSection>

          <ResizableSection title="Transmission Narrative" icon="≡" accent="#4C8DFF" storageKey="tx-narrative" defaultHeight={240}>
            <div style={{ minWidth: 0 }}>
              <TransmissionNarrativePanel
                prioritization={props.aiPrioritization}
                simulationResult={props.approveResult.simulation_result}
                anomalies={props.anomalies}
                isAiRecommendedPlan={
                  props.approveResult.simulation_result.plan_id !== undefined &&
                  props.approveResult.simulation_result.plan_id !== 'operator-override'
                }
              />
            </div>
          </ResizableSection>
        </>
      )}

      <ResizableSection title="Mission Report" icon="◉" accent="#4C8DFF" storageKey="mission-report" defaultHeight={360}>
        <div style={{ minWidth: 0, overflowX: 'hidden' }}>
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
        </div>
      </ResizableSection>
    </>
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

// ── RightPanel ────────────────────────────────────────────────────────────────

interface RightPanelProps extends CommonProps {
  section: NavSection;
  viewSettings: ViewSettings;
  onUpdateSetting: <K extends keyof ViewSettings>(key: K, value: ViewSettings[K]) => void;
  onResetSettings: () => void;
  onResetPanelWidth: () => void;
  panelWidth: number;
  panelDefaultWidth: number;
}

export function RightPanel({
  section,
  viewSettings,
  onUpdateSetting,
  onResetSettings,
  onResetPanelWidth,
  panelWidth,
  panelDefaultWidth,
  ...props
}: RightPanelProps) {
  const heading = SECTION_HEADINGS[section];

  function handleResetSectionSizes() {
    // no-op callback passed to ConfigPanel; actual localStorage clearing happens inside ConfigPanel
  }

  return (
    <div style={{
      width: panelWidth,
      minWidth: 340,
      maxWidth: 680,
      flexShrink: 0,
      background: '#0B0F18',
      borderLeft: '1px solid rgba(46,58,79,0.8)',
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
    }}>
      {/* Panel header */}
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
        </span>
      </div>

      {/* Scrollable content */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        overflowX: 'hidden',
        padding: '10px 10px',
        display: 'flex',
        flexDirection: 'column',
        gap: 0,
        minWidth: 0,
      }}>
        {section === 'mission'      && <MissionSection {...props} />}
        {section === 'spacecraft'   && <SpacecraftSection {...props} />}
        {section === 'comms'        && <CommsSection {...props} />}
        {section === 'data'         && <DataSection {...props} />}
        {section === 'ai'           && <AiSection {...props} />}
        {section === 'transmission' && <TransmissionSection {...props} />}
        {section === 'log'          && <LogSection {...props} />}
        {section === 'config'       && (
          <ConfigPanel
            settings={viewSettings}
            onUpdate={onUpdateSetting}
            onResetSettings={onResetSettings}
            onResetPanelWidth={onResetPanelWidth}
            onResetSectionSizes={handleResetSectionSizes}
            panelWidth={panelWidth}
            panelDefaultWidth={panelDefaultWidth}
          />
        )}
      </div>
    </div>
  );
}
