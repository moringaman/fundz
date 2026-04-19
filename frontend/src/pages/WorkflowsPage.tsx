import { useState, useEffect, useRef } from 'react';

// ─────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────
type NodeKind = 'start' | 'trigger' | 'process' | 'decision' | 'action' | 'success' | 'failure' | 'warning' | 'end';

interface FlowNode {
  id: string;
  kind: NodeKind;
  label: string;
  sub?: string;
  icon?: string;
  branches?: { label: string; to: string; pass?: boolean }[];
}

interface FlowLane {
  id: string;
  title: string;
  subtitle: string;
  interval: string;
  color: string;
  nodes: FlowNode[];
}

// ─────────────────────────────────────────────
// Workflow Data
// ─────────────────────────────────────────────
const LANES: FlowLane[] = [
  {
    id: 'tier1',
    title: 'Team Analysis',
    subtitle: 'Every 5 minutes',
    interval: '5m',
    color: '#00c2ff',
    nodes: [
      { id: 't1-start', kind: 'trigger', label: '5-min Tick', sub: 'Main loop fires every 60s\n5-min gate opens', icon: '⏱' },
      { id: 't1-marina', kind: 'process', label: 'Marina — Research', sub: 'Analyse all trading pairs\nDetect market regime & sentiment\nEmit StrategyRecommendations', icon: '🔬' },
      { id: 't1-market', kind: 'process', label: 'Market Condition', sub: 'Compute trend / volatility / RSI\nBuild confluence scores (TA)', icon: '📊' },
      { id: 't1-perf-gate', kind: 'action', label: 'Perf Gate Refresh', sub: 'DB trade data → _perf_mult [0.60–1.30]\nConf floor tiers per trader\nProbation + consistency flags', icon: '🎚' },
      { id: 't1-risk', kind: 'process', label: 'Elena — Risk Assessment', sub: 'Daily P&L vs max loss limit\nExposure % of capital\nEmit safe / warning / danger', icon: '🛡' },
      {
        id: 't1-risk-check', kind: 'decision', label: 'Risk level?',
        branches: [
          { label: 'danger', to: 't1-halt', pass: false },
          { label: 'safe / warning', to: 't1-continue', pass: true },
        ],
      },
      { id: 't1-halt', kind: 'failure', label: 'All agents blocked', sub: 'GATE 1 will skip every agent\nuntil next cycle resets risk', icon: '🚫' },
      { id: 't1-continue', kind: 'success', label: 'Trading active', sub: 'Agent tier proceeds normally', icon: '✅' },
    ],
  },
  {
    id: 'tier1b',
    title: 'Strategy Review',
    subtitle: 'Every 20 minutes',
    interval: '20m',
    color: '#a78bfa',
    nodes: [
      { id: 'sr-start', kind: 'trigger', label: '20-min Gate', sub: 'Strategy review + CIO\naligned every 20 min', icon: '⏱' },
      { id: 'sr-review', kind: 'process', label: 'FM + TA Review', sub: 'Score all agents\n40% perf · 30% fit · 30% conf\nMarina priority boosts grid', icon: '🔍' },
      {
        id: 'sr-decision', kind: 'decision', label: 'Actions proposed?',
        branches: [
          { label: 'None', to: 'sr-noop', pass: true },
          { label: 'Actions exist', to: 'sr-execute', pass: false },
        ],
      },
      { id: 'sr-noop', kind: 'action', label: 'No changes', sub: 'All agents performing adequately', icon: '😐' },
      { id: 'sr-execute', kind: 'action', label: 'Execute actions', sub: 'enable_agent · disable_agent\nadjust_params · create_agent\nBootstrap + cooldown on enable', icon: '⚡' },
      { id: 'sr-traders', kind: 'process', label: 'Trader Reviews', sub: 'Each trader runs manage_agents()\nMarina regime injected\nPropose per-trader changes', icon: '🧠' },
      { id: 'sr-cio', kind: 'process', label: 'CIO Report', sub: 'Strategic recommendations\nAllocation adjustments\nMap to trader-level changes', icon: '👔' },
      { id: 'sr-retro', kind: 'process', label: 'Trade Retrospective', sub: 'Analyse recent trades\nAuto-tune SL / TP / trailing\nLog learnings to team chat', icon: '📖' },
    ],
  },
  {
    id: 'tier2',
    title: 'Agent Execution',
    subtitle: 'Per agent schedule (default 1h)',
    interval: '1h',
    color: '#34d399',
    nodes: [
      { id: 'ag-start', kind: 'trigger', label: 'Agent scheduled', sub: 'run_interval_seconds elapsed\nfor this agent', icon: '🤖' },
      {
        id: 'ag-g0', kind: 'decision', label: 'GATE 0 — Interval',
        branches: [
          { label: 'Too soon', to: 'ag-skip0', pass: false },
          { label: 'Due', to: 'ag-g1', pass: true },
        ],
      },
      { id: 'ag-skip0', kind: 'warning', label: 'Skip cycle', sub: 'Agent will retry next loop', icon: '⏭' },
      {
        id: 'ag-g1', kind: 'decision', label: 'GATE 1 — Portfolio Risk',
        branches: [
          { label: 'DANGER', to: 'ag-skip1', pass: false },
          { label: 'Safe / Warning', to: 'ag-g2', pass: true },
        ],
      },
      { id: 'ag-skip1', kind: 'failure', label: 'Blocked by risk', sub: 'Elena flagged DANGER level\nAll new trades suspended', icon: '🛡' },
      {
        id: 'ag-g2', kind: 'decision', label: 'GATE 2 — Allocation',
        branches: [
          { label: 'allocation = 0%', to: 'ag-skip2', pass: false },
          { label: 'allocation > 0%', to: 'ag-dispatch', pass: true },
        ],
      },
      { id: 'ag-skip2', kind: 'failure', label: 'Zero allocation', sub: 'Portfolio Manager removed budget\nAgent cannot size a position', icon: '💸' },
      {
        id: 'ag-dispatch', kind: 'decision', label: 'Strategy type?',
        branches: [
          { label: 'grid', to: 'ag-grid', pass: false },
          { label: 'all others', to: 'ag-scan', pass: true },
        ],
      },
      { id: 'ag-grid', kind: 'action', label: 'Grid Engine path', sub: 'check_exit_conditions()\ninitialise / place levels\nmonitor fills & counters', icon: '🔲' },
      { id: 'ag-scan', kind: 'process', label: 'Scan trading pairs', sub: 'Fetch 200 candles per symbol\nGenerate indicators\nSelect best signal', icon: '📡' },
      {
        id: 'ag-g3', kind: 'decision', label: 'GATE 3 — Confidence ≥ 60%',
        branches: [
          { label: '< 60%', to: 'ag-hold', pass: false },
          { label: '≥ 60%', to: 'ag-g4', pass: true },
        ],
      },
      { id: 'ag-hold', kind: 'warning', label: 'HOLD', sub: 'Signal too weak\nNo action this cycle', icon: '⏸' },
      {
        id: 'ag-g4', kind: 'decision', label: 'GATE 4 — TP covers fees',
        branches: [
          { label: 'net_tp < 0.5%', to: 'ag-block4', pass: false },
          { label: 'net_tp ≥ 0.5%', to: 'ag-size', pass: true },
        ],
      },
      { id: 'ag-block4', kind: 'failure', label: 'TP blocked', sub: 'Fees eat the profit margin\nTrade not worth executing', icon: '💰' },
      { id: 'ag-size', kind: 'process', label: 'Size position', sub: 'total_fund × alloc% × strategy_mult\ncap at 95% available USDT', icon: '📐' },
      {
        id: 'ag-g6', kind: 'decision', label: 'GATE 5 — Risk Manager',
        branches: [
          { label: 'Rejected', to: 'ag-block6', pass: false },
          { label: 'Approved', to: 'ag-g7', pass: true },
        ],
      },
      { id: 'ag-block6', kind: 'failure', label: 'Elena vetoes', sub: 'Exposure too high\nor daily loss limit hit', icon: '🛑' },
      {
        id: 'ag-g7', kind: 'decision', label: 'GATE 6 — TA Confluence',
        branches: [
          { label: 'Opposite + >75% conf', to: 'ag-block7', pass: false },
          { label: 'Agrees or neutral', to: 'ag-g8', pass: true },
        ],
      },
      { id: 'ag-block7', kind: 'failure', label: 'Marcus vetoes', sub: 'Strong contrary TA signal\nTrade would fight the chart', icon: '📉' },
      {
        id: 'ag-g8', kind: 'decision', label: 'GATE 7 — Exec Coordinator',
        branches: [
          { label: 'Conflict', to: 'ag-block8', pass: false },
          { label: 'Approved', to: 'ag-g9', pass: true },
        ],
      },
      { id: 'ag-block8', kind: 'failure', label: 'Alex blocks', sub: 'Duplicate / over-concentrated\nposition in this cycle', icon: '🔁' },
      {
        id: 'ag-g9', kind: 'decision', label: 'GATE 8 — Backtest gate',
        branches: [
          { label: 'WR < 35% or < 5 trades', to: 'ag-hardblock', pass: false },
          { label: 'WR ≥ 35%, net ≤ 0', to: 'ag-softblock', pass: false },
          { label: 'WR ≥ 35%, net > 0', to: 'ag-execute', pass: true },
        ],
      },
      { id: 'ag-hardblock', kind: 'failure', label: 'Hard block', sub: 'No statistical edge detected\nTrade cancelled entirely', icon: '⛔' },
      { id: 'ag-softblock', kind: 'warning', label: 'Soft reduce 50%', sub: 'Edge present but small\nPosition halved, trade proceeds', icon: '⚠️' },
      { id: 'ag-execute', kind: 'success', label: 'Place order', sub: 'paper_trading.place_order()\nSL · TP · trailing · scale-out levels\nLog trade intent to team chat', icon: '🚀' },
    ],
  },
  {
    id: 'tier3',
    title: 'Position Monitor',
    subtitle: 'Every 60 seconds',
    interval: '60s',
    color: '#fb923c',
    nodes: [
      { id: 'pm-start', kind: 'trigger', label: 'Every 60s tick', sub: 'Runs on main loop\nfor all open positions', icon: '💹' },
      { id: 'pm-price', kind: 'process', label: 'Fetch live price', sub: 'Current price vs entry\nCompute unrealized P&L %', icon: '📈' },
      {
        id: 'pm-sl', kind: 'decision', label: 'Stop-loss hit?',
        branches: [
          { label: 'Long: price ≤ SL\nShort: price ≥ SL', to: 'pm-exit-sl', pass: false },
          { label: 'No', to: 'pm-tp', pass: true },
        ],
      },
      { id: 'pm-exit-sl', kind: 'failure', label: 'Exit — Stop Loss', sub: 'Close full position\nRecord negative P&L\nSend Telegram alert', icon: '🔴' },
      {
        id: 'pm-tp', kind: 'decision', label: 'Take-profit hit?',
        branches: [
          { label: 'Long: price ≥ TP\nShort: price ≤ TP', to: 'pm-exit-tp', pass: false },
          { label: 'No', to: 'pm-trail', pass: true },
        ],
      },
      { id: 'pm-exit-tp', kind: 'success', label: 'Exit — Take Profit', sub: 'Close full position\nRecord positive P&L\nSend Telegram alert 🎉', icon: '🟢' },
      {
        id: 'pm-trail', kind: 'decision', label: 'Trailing stop hit?',
        branches: [
          { label: 'Watermark\nbreached', to: 'pm-exit-trail', pass: false },
          { label: 'No', to: 'pm-be', pass: true },
        ],
      },
      { id: 'pm-exit-trail', kind: 'warning', label: 'Exit — Trailing Stop', sub: 'Watermark × (1 − trail%)\nCaptures locked-in gains', icon: '🌊' },
      {
        id: 'pm-be', kind: 'decision', label: 'Breakeven trigger? (33% to TP)',
        branches: [
          { label: 'Yes — first time', to: 'pm-move-be', pass: true },
          { label: 'No / done', to: 'pm-lock', pass: false },
        ],
      },
      { id: 'pm-move-be', kind: 'action', label: 'Move SL → breakeven', sub: 'SL set to entry ± fees\nProtects from loss on winner', icon: '⚓' },
      {
        id: 'pm-lock', kind: 'decision', label: 'Profit lock? (66% to TP)',
        branches: [
          { label: 'Yes — first time', to: 'pm-move-lock', pass: true },
          { label: 'No / done', to: 'pm-scale', pass: false },
        ],
      },
      { id: 'pm-move-lock', kind: 'action', label: 'SL → 50% of profit', sub: 'Locks in half the open gain\nContinues to ride', icon: '🔒' },
      {
        id: 'pm-scale', kind: 'decision', label: 'Scale-out level hit?',
        branches: [
          { label: 'Level reached', to: 'pm-partial', pass: true },
          { label: 'No', to: 'pm-hold', pass: false },
        ],
      },
      { id: 'pm-partial', kind: 'action', label: 'Partial close', sub: 'Close 25–50% of position\nBank profit slice\nMove SL to breakeven', icon: '✂️' },
      { id: 'pm-hold', kind: 'end', label: 'Hold — next tick', sub: 'Position in healthy range\nContinue monitoring', icon: '😌' },
    ],
  },
];

// ─────────────────────────────────────────────
// Node colours / styles
// ─────────────────────────────────────────────
const NODE_STYLES: Record<NodeKind, { bg: string; border: string; text: string; shape: string }> = {
  start:    { bg: 'rgba(0,194,255,0.12)',  border: '#00c2ff', text: '#7de8ff', shape: 'pill' },
  trigger:  { bg: 'rgba(0,194,255,0.08)',  border: 'rgba(0,194,255,0.4)', text: '#7de8ff', shape: 'pill' },
  process:  { bg: 'rgba(30,42,60,0.9)',    border: 'rgba(100,140,200,0.3)', text: '#c8d8f0', shape: 'rect' },
  decision: { bg: 'rgba(167,139,250,0.1)', border: 'rgba(167,139,250,0.5)', text: '#c4b5fd', shape: 'diamond' },
  action:   { bg: 'rgba(52,211,153,0.08)', border: 'rgba(52,211,153,0.3)', text: '#6ee7b7', shape: 'rect' },
  success:  { bg: 'rgba(34,197,94,0.1)',   border: 'rgba(34,197,94,0.5)', text: '#86efac', shape: 'rect' },
  failure:  { bg: 'rgba(239,68,68,0.1)',   border: 'rgba(239,68,68,0.4)', text: '#fca5a5', shape: 'rect' },
  warning:  { bg: 'rgba(251,191,36,0.08)', border: 'rgba(251,191,36,0.4)', text: '#fde68a', shape: 'rect' },
  end:      { bg: 'rgba(100,116,139,0.1)', border: 'rgba(100,116,139,0.3)', text: '#94a3b8', shape: 'pill' },
};

// ─────────────────────────────────────────────
// Animated "pulse" dot for active lane header
// ─────────────────────────────────────────────
function PulseDot({ color }: { color: string }) {
  return (
    <span style={{ position: 'relative', display: 'inline-block', width: 10, height: 10 }}>
      <span style={{
        position: 'absolute', inset: 0, borderRadius: '50%',
        background: color, opacity: 0.5,
        animation: 'wf-ping 1.6s cubic-bezier(0,0,0.2,1) infinite',
      }} />
      <span style={{
        position: 'relative', display: 'block', width: 10, height: 10,
        borderRadius: '50%', background: color,
      }} />
    </span>
  );
}

// ─────────────────────────────────────────────
// Single flow node
// ─────────────────────────────────────────────
function FlowNodeCard({
  node, laneColor, index, isActive,
}: {
  node: FlowNode; laneColor: string; index: number; isActive: boolean;
}) {
  const style = NODE_STYLES[node.kind];
  const isDiamond = style.shape === 'diamond';
  const isPill = style.shape === 'pill';

  return (
    <div
      style={{
        opacity: isActive ? 1 : 0,
        transform: isActive ? 'translateY(0)' : 'translateY(16px)',
        transition: `opacity 0.4s ease ${index * 0.06}s, transform 0.4s ease ${index * 0.06}s`,
      }}
    >
      {/* connector line above (not for first node) */}
      {index > 0 && (
        <div style={{ display: 'flex', justifyContent: 'center', height: 20 }}>
          <div style={{
            width: 1.5, height: '100%',
            background: `linear-gradient(to bottom, rgba(100,140,200,0.15), rgba(100,140,200,0.35))`,
          }} />
        </div>
      )}

      <div style={{
        background: style.bg,
        border: `1px solid ${node.kind === 'decision' ? laneColor + '60' : style.border}`,
        borderRadius: isPill ? 999 : isDiamond ? 0 : 10,
        transform: isDiamond ? 'rotate(0deg)' : 'none',
        padding: isDiamond ? '0.6rem 1.4rem' : '0.65rem 1rem',
        display: 'flex',
        alignItems: 'flex-start',
        gap: '0.6rem',
        boxShadow: isActive ? `0 0 0 0 ${laneColor}` : 'none',
        position: 'relative',
        overflow: 'visible',
      }}>
        {/* left accent bar */}
        {node.kind === 'decision' && (
          <div style={{
            position: 'absolute', left: 0, top: '15%', bottom: '15%',
            width: 3, background: laneColor, borderRadius: 99,
          }} />
        )}

        {node.icon && (
          <span style={{ fontSize: '1.05rem', flexShrink: 0, marginTop: 1 }}>{node.icon}</span>
        )}

        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            fontFamily: "'DM Mono', 'Fira Code', monospace",
            fontSize: '0.72rem',
            color: style.text,
            fontWeight: node.kind === 'decision' ? 700 : 600,
            letterSpacing: node.kind === 'decision' ? '0.04em' : '0.01em',
            marginBottom: node.sub ? '0.25rem' : 0,
            lineHeight: 1.3,
          }}>
            {node.kind === 'decision' && (
              <span style={{
                display: 'inline-block', marginRight: '0.35rem',
                fontSize: '0.6rem', padding: '0.05rem 0.35rem',
                background: laneColor + '20', color: laneColor,
                border: `1px solid ${laneColor}40`, borderRadius: 4,
                verticalAlign: 'middle', fontWeight: 700, letterSpacing: '0.05em',
              }}>◆</span>
            )}
            {node.label}
          </div>
          {node.sub && (
            <div style={{
              fontFamily: "'DM Sans', 'IBM Plex Sans', sans-serif",
              fontSize: '0.62rem',
              color: 'rgba(160,180,210,0.7)',
              lineHeight: 1.5,
              whiteSpace: 'pre-line',
            }}>
              {node.sub}
            </div>
          )}
        </div>

        {/* badge for kind */}
        {(node.kind === 'success' || node.kind === 'failure' || node.kind === 'warning') && (
          <span style={{
            fontSize: '0.6rem', padding: '0.1rem 0.4rem', borderRadius: 4,
            background: node.kind === 'success' ? 'rgba(34,197,94,0.15)'
              : node.kind === 'failure' ? 'rgba(239,68,68,0.15)'
              : 'rgba(251,191,36,0.15)',
            color: node.kind === 'success' ? '#86efac'
              : node.kind === 'failure' ? '#fca5a5'
              : '#fde68a',
            border: `1px solid ${node.kind === 'success' ? 'rgba(34,197,94,0.3)'
              : node.kind === 'failure' ? 'rgba(239,68,68,0.3)'
              : 'rgba(251,191,36,0.3)'}`,
            fontFamily: 'monospace', fontWeight: 700, flexShrink: 0, alignSelf: 'flex-start',
          }}>
            {node.kind === 'success' ? 'PASS' : node.kind === 'failure' ? 'FAIL' : 'WARN'}
          </span>
        )}
      </div>

      {/* Branch labels */}
      {node.branches && node.branches.length > 0 && (
        <div style={{
          display: 'flex', gap: '0.5rem', justifyContent: 'center',
          flexWrap: 'wrap', marginTop: '0.35rem',
        }}>
          {node.branches.map((b, i) => (
            <span key={i} style={{
              fontSize: '0.58rem',
              fontFamily: "'DM Mono', monospace",
              padding: '0.1rem 0.5rem',
              borderRadius: 4,
              background: b.pass ? 'rgba(52,211,153,0.08)' : 'rgba(239,68,68,0.08)',
              color: b.pass ? '#6ee7b7' : '#fca5a5',
              border: `1px solid ${b.pass ? 'rgba(52,211,153,0.2)' : 'rgba(239,68,68,0.2)'}`,
              whiteSpace: 'pre-line',
              textAlign: 'center',
            }}>
              {b.pass ? '✓' : '✗'} {b.label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────
// Lane column
// ─────────────────────────────────────────────
function Lane({ lane, isActive }: { lane: FlowLane; isActive: boolean }) {
  return (
    <div style={{
      minWidth: 280, maxWidth: 320, flex: '0 0 300px',
      display: 'flex', flexDirection: 'column', gap: 0,
    }}>
      {/* Lane header */}
      <div style={{
        background: `linear-gradient(135deg, ${lane.color}14, ${lane.color}06)`,
        border: `1px solid ${lane.color}30`,
        borderRadius: '12px 12px 0 0',
        padding: '0.85rem 1.1rem',
        display: 'flex', alignItems: 'center', gap: '0.6rem',
        marginBottom: 0,
      }}>
        <PulseDot color={lane.color} />
        <div>
          <div style={{
            fontFamily: "'DM Mono', monospace", fontSize: '0.78rem',
            color: lane.color, fontWeight: 700, letterSpacing: '0.04em',
          }}>{lane.title}</div>
          <div style={{
            fontFamily: "'DM Sans', sans-serif", fontSize: '0.6rem',
            color: 'rgba(160,180,210,0.6)', marginTop: '0.1rem',
          }}>{lane.subtitle}</div>
        </div>
        <div style={{ marginLeft: 'auto' }}>
          <span style={{
            fontFamily: 'monospace', fontSize: '0.65rem',
            background: lane.color + '18', color: lane.color,
            border: `1px solid ${lane.color}35`,
            padding: '0.15rem 0.5rem', borderRadius: 6,
          }}>{lane.interval}</span>
        </div>
      </div>

      {/* Lane body */}
      <div style={{
        background: 'rgba(10,18,30,0.7)',
        border: `1px solid ${lane.color}15`,
        borderTop: 'none',
        borderRadius: '0 0 12px 12px',
        padding: '1rem 0.85rem 1.25rem',
        flex: 1,
      }}>
        {lane.nodes.map((node, i) => (
          <FlowNodeCard
            key={node.id}
            node={node}
            laneColor={lane.color}
            index={i}
            isActive={isActive}
          />
        ))}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────
// Legend
// ─────────────────────────────────────────────
function Legend() {
  const items = [
    { kind: 'trigger' as NodeKind, label: 'Trigger' },
    { kind: 'process' as NodeKind, label: 'Process' },
    { kind: 'decision' as NodeKind, label: 'Decision' },
    { kind: 'action' as NodeKind, label: 'Action' },
    { kind: 'success' as NodeKind, label: 'Pass' },
    { kind: 'warning' as NodeKind, label: 'Warning' },
    { kind: 'failure' as NodeKind, label: 'Fail / Block' },
  ];
  return (
    <div style={{
      display: 'flex', flexWrap: 'wrap', gap: '0.5rem', alignItems: 'center',
      padding: '0.6rem 1rem',
      background: 'rgba(10,18,30,0.6)',
      border: '1px solid rgba(100,140,200,0.1)',
      borderRadius: 10,
      marginBottom: '1.5rem',
    }}>
      <span style={{ fontSize: '0.6rem', color: 'rgba(160,180,210,0.5)', fontFamily: 'monospace', marginRight: '0.25rem' }}>LEGEND</span>
      {items.map(({ kind, label }) => {
        const s = NODE_STYLES[kind];
        return (
          <span key={kind} style={{
            fontSize: '0.6rem', padding: '0.15rem 0.5rem',
            background: s.bg, color: s.text,
            border: `1px solid ${s.border}`,
            borderRadius: kind === 'decision' ? 4 : kind === 'trigger' ? 99 : 5,
            fontFamily: "'DM Mono', monospace",
          }}>
            {kind === 'decision' && '◆ '}{label}
          </span>
        );
      })}
    </div>
  );
}

// ─────────────────────────────────────────────
// Animated ticker showing what fires when
// ─────────────────────────────────────────────
function CycleTicker() {
  const items = [
    { label: 'Position monitor', interval: '60s', color: '#fb923c' },
    { label: 'Team analysis', interval: '5m', color: '#00c2ff' },
    { label: 'Strategy review', interval: '20m', color: '#a78bfa' },
    { label: 'CIO + Retro', interval: '20m', color: '#a78bfa' },
    { label: 'Agent execution', interval: '1h', color: '#34d399' },
    { label: 'Daily report', interval: '24h', color: '#f59e0b' },
  ];
  return (
    <div style={{
      display: 'flex', gap: '0.75rem', flexWrap: 'wrap', marginBottom: '1.75rem',
    }}>
      {items.map((item, i) => (
        <div key={i} style={{
          display: 'flex', alignItems: 'center', gap: '0.4rem',
          background: 'rgba(10,18,30,0.8)',
          border: `1px solid ${item.color}25`,
          borderRadius: 8, padding: '0.4rem 0.75rem',
          animation: `wf-fadein 0.5s ease ${i * 0.1}s both`,
        }}>
          <PulseDot color={item.color} />
          <span style={{ fontFamily: "'DM Mono', monospace", fontSize: '0.65rem', color: 'rgba(200,220,245,0.8)' }}>{item.label}</span>
          <span style={{
            fontFamily: 'monospace', fontSize: '0.58rem',
            background: item.color + '18', color: item.color,
            padding: '0.05rem 0.35rem', borderRadius: 4,
            border: `1px solid ${item.color}30`,
          }}>{item.interval}</span>
        </div>
      ))}
    </div>
  );
}

// ─────────────────────────────────────────────
// Data flow diagram (mini)
// ─────────────────────────────────────────────
function DataFlowDiagram() {
  const flows = [
    { from: 'TIER 1', arrow: '→', to: 'TIER 2', via: '_current_risk_assessment\n_current_allocation\n_current_analyst_report', color: '#00c2ff' },
    { from: 'TIER 2', arrow: '→', to: 'TIER 3', via: 'position.sl · position.tp\nposition.trailing_stop_pct\nposition.scale_out_levels', color: '#34d399' },
    { from: 'TIER 1', arrow: '→', to: 'TIER 3', via: 'confluence_scores (SL/TP review)\nrisk_assessment (exit prompt)', color: '#a78bfa' },
    { from: 'TIER 2', arrow: '→', to: 'TIER 2 (next agent)', via: '_cycle_trades buffer\n(Alex conflict check)', color: '#fb923c' },
  ];

  return (
    <div style={{ marginBottom: '2rem' }}>
      <div style={{
        fontFamily: "'DM Mono', monospace", fontSize: '0.65rem',
        color: 'rgba(160,180,210,0.5)', letterSpacing: '0.08em',
        textTransform: 'uppercase', marginBottom: '0.75rem',
      }}>Data Flow Between Tiers</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.6rem' }}>
        {flows.map((f, i) => (
          <div key={i} style={{
            background: 'rgba(10,18,30,0.8)',
            border: `1px solid ${f.color}20`,
            borderRadius: 8, padding: '0.6rem 0.85rem',
            minWidth: 220,
            animation: `wf-fadein 0.5s ease ${0.3 + i * 0.1}s both`,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.3rem' }}>
              <span style={{
                fontFamily: 'monospace', fontSize: '0.65rem',
                color: f.color, fontWeight: 700,
                padding: '0.1rem 0.4rem', background: f.color + '15',
                border: `1px solid ${f.color}30`, borderRadius: 4,
              }}>{f.from}</span>
              <span style={{ color: 'rgba(160,180,210,0.4)', fontSize: '0.8rem' }}>→</span>
              <span style={{
                fontFamily: 'monospace', fontSize: '0.65rem',
                color: f.color, fontWeight: 700,
                padding: '0.1rem 0.4rem', background: f.color + '15',
                border: `1px solid ${f.color}30`, borderRadius: 4,
              }}>{f.to}</span>
            </div>
            <div style={{
              fontFamily: "'DM Sans', sans-serif", fontSize: '0.6rem',
              color: 'rgba(140,160,200,0.65)', lineHeight: 1.6,
              whiteSpace: 'pre-line',
            }}>{f.via}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────
// Main page
// ─────────────────────────────────────────────
export function WorkflowsPage() {
  const [activeLanes, setActiveLanes] = useState<Set<string>>(new Set());
  const [selectedLane, setSelectedLane] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // staggered lane reveal on mount
  useEffect(() => {
    const ids = LANES.map(l => l.id);
    ids.forEach((id, i) => {
      setTimeout(() => {
        setActiveLanes(prev => new Set([...prev, id]));
      }, i * 150 + 100);
    });
  }, []);

  const visibleLanes = selectedLane
    ? LANES.filter(l => l.id === selectedLane)
    : LANES;

  return (
    <div ref={containerRef} style={{
      padding: '1.5rem 1.5rem 3rem',
      minHeight: '100vh',
      background: 'var(--bg-primary, #0a1220)',
      fontFamily: "'DM Sans', 'IBM Plex Sans', sans-serif",
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500;700&family=DM+Sans:wght@300;400;500;600&display=swap');

        @keyframes wf-ping {
          75%, 100% { transform: scale(2.2); opacity: 0; }
        }
        @keyframes wf-fadein {
          from { opacity: 0; transform: translateY(10px); }
          to   { opacity: 1; transform: translateY(0);    }
        }
        @keyframes wf-scan {
          0%   { background-position: 0% 0%; }
          100% { background-position: 0% 100%; }
        }
        .wf-scroll::-webkit-scrollbar { height: 5px; }
        .wf-scroll::-webkit-scrollbar-track { background: rgba(0,0,0,0.2); }
        .wf-scroll::-webkit-scrollbar-thumb { background: rgba(0,194,255,0.25); border-radius: 99px; }
      `}</style>

      {/* Header */}
      <div style={{ marginBottom: '2rem', animation: 'wf-fadein 0.6s ease both' }}>
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: '1rem', flexWrap: 'wrap' }}>
          <div>
            <div style={{
              fontFamily: "'DM Mono', monospace",
              fontSize: '0.6rem', letterSpacing: '0.15em',
              color: 'rgba(0,194,255,0.6)', textTransform: 'uppercase',
              marginBottom: '0.35rem',
            }}>Automation Decision Tree</div>
            <h1 style={{
              fontFamily: "'DM Mono', monospace",
              fontSize: 'clamp(1.3rem, 2.5vw, 1.75rem)',
              fontWeight: 700, color: '#e2eaf8',
              letterSpacing: '-0.02em', margin: 0,
            }}>System Workflows</h1>
          </div>
          <div style={{
            marginLeft: 'auto', display: 'flex', gap: '0.5rem', flexWrap: 'wrap',
          }}>
            <button
              type="button"
              onClick={() => setSelectedLane(null)}
              style={{
                fontFamily: "'DM Mono', monospace", fontSize: '0.62rem',
                padding: '0.35rem 0.75rem', borderRadius: 6, cursor: 'pointer',
                background: selectedLane === null ? 'rgba(0,194,255,0.15)' : 'rgba(20,32,52,0.8)',
                color: selectedLane === null ? '#00c2ff' : 'rgba(160,180,210,0.7)',
                border: selectedLane === null ? '1px solid rgba(0,194,255,0.35)' : '1px solid rgba(100,140,200,0.2)',
                transition: 'all 0.2s',
              }}
            >All tiers</button>
            {LANES.map(l => (
              <button
                key={l.id}
                type="button"
                onClick={() => setSelectedLane(selectedLane === l.id ? null : l.id)}
                style={{
                  fontFamily: "'DM Mono', monospace", fontSize: '0.62rem',
                  padding: '0.35rem 0.75rem', borderRadius: 6, cursor: 'pointer',
                  background: selectedLane === l.id ? l.color + '18' : 'rgba(20,32,52,0.8)',
                  color: selectedLane === l.id ? l.color : 'rgba(160,180,210,0.7)',
                  border: `1px solid ${selectedLane === l.id ? l.color + '40' : 'rgba(100,140,200,0.2)'}`,
                  transition: 'all 0.2s',
                }}
              >{l.title}</button>
            ))}
          </div>
        </div>
      </div>

      {/* Cycle timers */}
      <CycleTicker />

      {/* Legend */}
      <Legend />

      {/* Data flow */}
      <DataFlowDiagram />

      {/* Workflow lanes */}
      <div style={{ marginBottom: '0.75rem', fontFamily: "'DM Mono', monospace", fontSize: '0.6rem', color: 'rgba(160,180,210,0.4)', letterSpacing: '0.08em', textTransform: 'uppercase' }}>
        Decision Flows
      </div>
      <div
        className="wf-scroll"
        style={{
          display: 'flex',
          gap: '1rem',
          overflowX: selectedLane ? 'visible' : 'auto',
          paddingBottom: '1rem',
          alignItems: 'flex-start',
          flexWrap: selectedLane ? 'wrap' : 'nowrap',
        }}
      >
        {visibleLanes.map(lane => (
          <Lane
            key={lane.id}
            lane={lane}
            isActive={activeLanes.has(lane.id)}
          />
        ))}
      </div>

      {/* Gate summary table */}
      <div style={{ marginTop: '2rem', animation: 'wf-fadein 0.6s ease 0.5s both' }}>
        <div style={{
          fontFamily: "'DM Mono', monospace", fontSize: '0.6rem',
          color: 'rgba(160,180,210,0.4)', letterSpacing: '0.08em',
          textTransform: 'uppercase', marginBottom: '0.75rem',
        }}>Agent Execution Gates Summary</div>
        <div style={{
          background: 'rgba(10,18,30,0.8)',
          border: '1px solid rgba(52,211,153,0.15)',
          borderRadius: 10, overflow: 'hidden',
        }}>
          <div style={{
            display: 'grid',
            gridTemplateColumns: '2.5rem 1fr 1.5fr 1fr 1fr',
            gap: 0,
          }}>
            {/* header */}
            {['#', 'Gate', 'Pass condition', 'Fail result', 'Frequency'].map(h => (
              <div key={h} style={{
                fontFamily: "'DM Mono', monospace", fontSize: '0.6rem',
                color: 'rgba(52,211,153,0.7)', padding: '0.5rem 0.75rem',
                borderBottom: '1px solid rgba(52,211,153,0.12)',
                background: 'rgba(52,211,153,0.04)',
                letterSpacing: '0.05em',
              }}>{h}</div>
            ))}
            {[
              ['0', 'Interval', 'time_since_run ≥ interval', 'Skip cycle', 'Per loop'],
              ['1', 'Portfolio Risk', 'risk_level ≠ danger', 'Block all agents', 'Per loop'],
              ['2', 'Allocation', 'allocation_pct > 0%', 'Zero-budget skip', 'Per loop'],
              ['3', 'Confidence', 'signal ≥ 60%', 'HOLD, no trade', 'Per signal'],
              ['4', 'TP covers fees', 'net_tp ≥ 0.5%', 'Trade blocked', 'Per trade'],
              ['5', 'Risk Manager', 'Elena approves', 'Rejected + alert', 'Per trade'],
              ['6', 'TA Confluence', 'Agrees or neutral', 'Marcus veto', 'Per trade'],
              ['7', 'Exec Coordinator', 'No conflict', 'Alex blocks', 'Per trade'],
              ['8', 'Backtest gate', 'WR ≥ 35% + net > 0', 'Hard block / 50% reduce', '4h cache'],
            ].map((row, ri) => (
              row.map((cell, ci) => (
                <div key={`${ri}-${ci}`} style={{
                  fontFamily: ci === 0 ? "'DM Mono', monospace" : "'DM Sans', sans-serif",
                  fontSize: ci === 0 ? '0.62rem' : '0.63rem',
                  color: ci === 2 ? 'rgba(110,231,183,0.8)'
                    : ci === 3 ? 'rgba(252,165,165,0.8)'
                    : 'rgba(180,200,230,0.75)',
                  padding: '0.45rem 0.75rem',
                  borderBottom: ri < 8 ? '1px solid rgba(100,140,200,0.07)' : 'none',
                  background: ri % 2 === 0 ? 'transparent' : 'rgba(30,45,70,0.2)',
                  fontWeight: ci === 1 ? 500 : 400,
                }}>{cell}</div>
              ))
            ))}
          </div>
        </div>
      </div>

      {/* Position exit conditions */}
      <div style={{ marginTop: '1.5rem', animation: 'wf-fadein 0.6s ease 0.7s both' }}>
        <div style={{
          fontFamily: "'DM Mono', monospace", fontSize: '0.6rem',
          color: 'rgba(160,180,210,0.4)', letterSpacing: '0.08em',
          textTransform: 'uppercase', marginBottom: '0.75rem',
        }}>Position Exit Triggers (every 60s)</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.6rem' }}>
          {[
            { label: 'Stop Loss', desc: 'price ≤ SL (long) / price ≥ SL (short)', color: '#ef4444', icon: '🔴' },
            { label: 'Take Profit', desc: 'price ≥ TP (long) / price ≤ TP (short)', color: '#22c55e', icon: '🟢' },
            { label: 'Trailing Stop', desc: 'watermark × (1 − trail%) breached', color: '#f59e0b', icon: '🌊' },
            { label: 'Breakeven SL', desc: '33% to TP → SL moves to entry ± fees', color: '#00c2ff', icon: '⚓' },
            { label: 'Profit Lock', desc: '66% to TP → SL locks in 50% gain', color: '#a78bfa', icon: '🔒' },
            { label: 'Scale-out', desc: 'Level threshold → close 25–50% slice', color: '#fb923c', icon: '✂️' },
          ].map((item, i) => (
            <div key={i} style={{
              background: 'rgba(10,18,30,0.8)',
              border: `1px solid ${item.color}20`,
              borderRadius: 8, padding: '0.6rem 0.85rem',
              display: 'flex', alignItems: 'flex-start', gap: '0.5rem',
              minWidth: 200, flex: '1 1 200px',
              animation: `wf-fadein 0.4s ease ${0.8 + i * 0.07}s both`,
            }}>
              <span style={{ fontSize: '1rem', flexShrink: 0 }}>{item.icon}</span>
              <div>
                <div style={{
                  fontFamily: "'DM Mono', monospace", fontSize: '0.68rem',
                  color: item.color, fontWeight: 600, marginBottom: '0.2rem',
                }}>{item.label}</div>
                <div style={{
                  fontFamily: "'DM Sans', sans-serif", fontSize: '0.61rem',
                  color: 'rgba(150,170,210,0.7)', lineHeight: 1.4,
                }}>{item.desc}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
