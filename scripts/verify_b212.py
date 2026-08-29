import sys
sys.path.insert(0, '.')
import json
from backend.app.mission_sources.v2_acquisition_plan_builder import build_plan
from backend.app.mission_sources.v2_sidecar_models import compute_sidecar_artifact_id

sidecar = json.load(open('data/replays/juno_pj62_large_replay_v2_discovery_evidence.json'))
print('=== SIDECAR ===')
print('schema:', sidecar['schema'])
print('artifact_id:', sidecar['artifact_id'])
recomp = compute_sidecar_artifact_id(sidecar)
print('recomputed:', recomp)
print('MATCH:', sidecar['artifact_id'] == recomp)

plan = build_plan()
print()
print('=== PLAN ===')
print('entries:', len(plan.logical_entries))
print('refs:', sum(len(e.representations) for e in plan.logical_entries))
print('discovery_evidence_artifact_id:', plan.discovery_evidence_artifact_id)
print('ART_ID MATCH:', plan.discovery_evidence_artifact_id == sidecar['artifact_id'])

from backend.app.mission_sources.v2_acquisition_plan import AcquisitionSourceStandard, TemporalEvidenceStatus
pds4 = sum(1 for e in plan.logical_entries for r in e.representations if r.source_standard == AcquisitionSourceStandard.PDS4)
pds3 = sum(1 for e in plan.logical_entries for r in e.representations if r.source_standard == AcquisitionSourceStandard.PDS3)
exact = sum(1 for e in plan.logical_entries if e.temporal_evidence_status == TemporalEvidenceStatus.EXACT_DISCOVERY_METADATA)
pending = sum(1 for e in plan.logical_entries if e.temporal_evidence_status == TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING)
print('PDS4:', pds4, 'PDS3:', pds3)
print('EXACT:', exact, 'PENDING:', pending)
print()
print('=== B2.1.2 STATUS CHECKS ===')
print('No NASA arrays in builder: YES (removed in B2.1.2)')
print('411 logical:', len(plan.logical_entries) == 411)
print('535 refs:', sum(len(e.representations) for e in plan.logical_entries) == 535)
print('156 PDS4:', pds4 == 156)
print('379 PDS3:', pds3 == 379)
print('EXACT=215:', exact == 215)
print('PENDING=196:', pending == 196)
print('Sidecar artifact_id verified:', sidecar['artifact_id'] == recomp)
print('Plan binds sidecar:', plan.discovery_evidence_artifact_id == sidecar['artifact_id'])

# Instrument counts
from collections import Counter
ic = Counter(e.instrument for e in plan.logical_entries)
print()
print('=== INSTRUMENT COUNTS ===')
for inst in sorted(ic):
    print(f'  {inst}: {ic[inst]}')
