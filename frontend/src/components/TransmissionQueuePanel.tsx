import type { CandidatePlan } from '../types/domain';

interface Props {
  plan: CandidatePlan;
}

export function TransmissionQueuePanel({ plan }: Props) {
  return (
    <section className="panel">
      <h2>Baseline Plan <small style={{ textTransform: 'none', letterSpacing: 0, fontWeight: 400 }}>({plan.strategy})</small></h2>
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>ID</th>
            <th>Type</th>
            <th>Size</th>
            <th>Criticality</th>
            <th>Relevance</th>
            <th>Deadline</th>
            <th>Delivery</th>
          </tr>
        </thead>
        <tbody>
          {plan.packets.map((pkt, i) => (
            <tr key={pkt.packet_id}>
              <td>{i + 1}</td>
              <td><code>{pkt.packet_id}</code></td>
              <td>{pkt.packet_type}</td>
              <td>{(pkt.size_bits / 1024).toFixed(1)} kb</td>
              <td>{pkt.criticality.toFixed(2)}</td>
              <td>{pkt.mission_relevance.toFixed(2)}</td>
              <td>{pkt.deadline_s.toFixed(1)} s</td>
              <td>{pkt.delivery_requirement}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
