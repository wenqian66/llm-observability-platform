import { useState, useEffect } from "react";
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, PieChart, Pie, Cell, Legend,
} from "recharts";

const COLORS = ["#4f46e5", "#06b6d4", "#f59e0b", "#ef4444", "#10b981", "#8b5cf6"];
const RANGES = [
  { key: "1d", label: "Today" },
  { key: "1w", label: "This Week" },
  { key: "1m", label: "This Month" },
  { key: "all", label: "All Time" },
];

function Card({ title, value, sub }) {
  return (
    <div style={{ background: "#1e1e2e", borderRadius: 12, padding: "20px 24px", minWidth: 180 }}>
      <div style={{ color: "#94a3b8", fontSize: 13, marginBottom: 4 }}>{title}</div>
      <div style={{ color: "#f1f5f9", fontSize: 28, fontWeight: 700 }}>{value}</div>
      {sub && <div style={{ color: "#64748b", fontSize: 12, marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

function App() {
  const [range, setRange] = useState("1d");
  const [metrics, setMetrics] = useState(null);
  const [hourly, setHourly] = useState([]);
  const [daily, setDaily] = useState([]);
  const [costByModel, setCostByModel] = useState([]);

  useEffect(() => {
    fetch(`/metrics/all?range=${range}`).then(r => r.json()).then(setMetrics).catch(() => {});
    fetch(`/metrics/cost-by-model?range=${range}`).then(r => r.json()).then(setCostByModel).catch(() => {});
  }, [range]);

  useEffect(() => {
    fetch("/metrics/chart/hourly-throughput").then(r => r.json()).then(setHourly).catch(() => {});
    fetch("/metrics/all/chart/daily-pattern").then(r => r.json()).then(setDaily).catch(() => {});
  }, []);

  return (
    <div style={{ fontFamily: "'Inter', system-ui, sans-serif", background: "#0f0f1a", color: "#e2e8f0", minHeight: "100vh", padding: "32px 48px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 32 }}>
        <h1 style={{ fontSize: 28, fontWeight: 700, margin: 0 }}>EchoLog Analytics</h1>
        <div style={{ display: "flex", gap: 8 }}>
          {RANGES.map(r => (
            <button
              key={r.key}
              onClick={() => setRange(r.key)}
              style={{
                padding: "6px 16px", borderRadius: 8, border: "none", cursor: "pointer",
                background: range === r.key ? "#4f46e5" : "#1e1e2e",
                color: range === r.key ? "#fff" : "#94a3b8",
                fontWeight: 500, fontSize: 13,
              }}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {/* Scorecards */}
      {metrics && (
        <div style={{ display: "flex", gap: 16, marginBottom: 32, flexWrap: "wrap" }}>
          <Card title="Requests Served" value={metrics.requests_served.toLocaleString()} />
          <Card title="Failed" value={metrics.requests_failed} />
          <Card title="Avg Latency" value={`${metrics.avg_latency}ms`} />
          <Card title="Avg Input Tokens" value={Math.round(metrics.avg_input_tokens).toLocaleString()} />
          <Card title="Avg Output Tokens" value={Math.round(metrics.avg_output_tokens).toLocaleString()} />
          <Card title="Total Tokens" value={(metrics.total_input_tokens + metrics.total_output_tokens).toLocaleString()} />
          <Card title="Est. Cost" value={`$${metrics.estimated_cost_usd.toFixed(2)}`} />
        </div>
      )}

      {/* Charts */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24, marginBottom: 32 }}>
        {/* Throughput */}
        <div style={{ background: "#1e1e2e", borderRadius: 12, padding: 24 }}>
          <h3 style={{ margin: "0 0 16px", fontSize: 15, color: "#94a3b8" }}>Hourly Throughput (24h)</h3>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={hourly}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
              <XAxis dataKey="hour" stroke="#64748b" fontSize={11} />
              <YAxis stroke="#64748b" fontSize={11} />
              <Tooltip contentStyle={{ background: "#1e293b", border: "none", borderRadius: 8 }} />
              <Bar dataKey="requests" fill="#4f46e5" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Latency */}
        <div style={{ background: "#1e1e2e", borderRadius: 12, padding: 24 }}>
          <h3 style={{ margin: "0 0 16px", fontSize: 15, color: "#94a3b8" }}>Avg Latency by Hour (24h)</h3>
          <ResponsiveContainer width="100%" height={250}>
            <LineChart data={hourly}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
              <XAxis dataKey="hour" stroke="#64748b" fontSize={11} />
              <YAxis stroke="#64748b" fontSize={11} unit="ms" />
              <Tooltip contentStyle={{ background: "#1e293b", border: "none", borderRadius: 8 }} />
              <Line type="monotone" dataKey="avg_latency_ms" stroke="#06b6d4" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>

        {/* Daily pattern */}
        <div style={{ background: "#1e1e2e", borderRadius: 12, padding: 24 }}>
          <h3 style={{ margin: "0 0 16px", fontSize: 15, color: "#94a3b8" }}>Requests by Day of Week</h3>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={daily}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
              <XAxis dataKey="day" stroke="#64748b" fontSize={11} />
              <YAxis stroke="#64748b" fontSize={11} />
              <Tooltip contentStyle={{ background: "#1e293b", border: "none", borderRadius: 8 }} />
              <Bar dataKey="value" fill="#10b981" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Cost by model */}
        <div style={{ background: "#1e1e2e", borderRadius: 12, padding: 24 }}>
          <h3 style={{ margin: "0 0 16px", fontSize: 15, color: "#94a3b8" }}>Cost by Model</h3>
          <ResponsiveContainer width="100%" height={250}>
            <PieChart>
              <Pie
                data={costByModel}
                dataKey="estimated_cost_usd"
                nameKey="model"
                cx="50%"
                cy="50%"
                outerRadius={90}
                label={({ model, estimated_cost_usd }) => `${model}: $${estimated_cost_usd.toFixed(2)}`}
                labelLine={false}
              >
                {costByModel.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip contentStyle={{ background: "#1e293b", border: "none", borderRadius: 8 }} />
              <Legend />
            </PieChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

export default App;
