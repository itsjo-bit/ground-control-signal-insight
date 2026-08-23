import type { LinkState } from '../types/domain';
import { SignalWaveform } from './SignalWaveform';

interface Props {
  linkState: LinkState;
}

export function LinkHealthPanel({ linkState: ls }: Props) {
  return (
    <section className="panel">
<h2>Link Health</h2>
<div className="waveform-wrap">
<SignalWaveform snrDb={ls.snr_db} width={340} height={60} />
</div>
<table>
<tbody>
<tr>
<td>SNR</td>
<td>{ls.snr_db.toFixed(1)} dB</td>
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
<td>Latency</td>
<td>{ls.latency_s.toFixed(3)} s</td>
</tr>
</tbody>
</table>
</section>
  );
}
