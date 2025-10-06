import { useState, useEffect } from "react";

const ensureUTC = (iso) => {
    return /Z$|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z";
};

const fmtCT = (iso) =>
  new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Chicago",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
  }).format(new Date(ensureUTC(iso)));

const DEFAULT_MODEL = "gemini-2.5-flash-lite";

function App() {
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState(DEFAULT_MODEL);
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(false);

  const fetchLogs = async () => {
    const res = await fetch("/api/requests");
    const data = await res.json();
    setLogs(data.reverse());
  };

  const sendPrompt = async () => {
    if (!prompt.trim()) return;
    setLoading(true);
    const res = await fetch("/api/llm/invoke", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: "gemini",
        model: model || DEFAULT_MODEL,
        prompt,
      }),
    });
    await res.json();
    setLoading(false);
    setPrompt("");
    await fetchLogs();
  };

  useEffect(() => {
    fetchLogs();
  }, []);

  return (
    <div style={{ fontFamily: "sans-serif", margin: "2rem" }}>
      <h1 style={{ fontSize: "3rem", marginBottom: "1.5rem" }}>
        LLM Observability Dashboard
      </h1>

      <div style={{ display: "flex", gap: "12px", marginBottom: "1rem" }}>
        <input
          style={{ width: "420px", padding: "8px" }}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Enter prompt..."
        />

        <select
          value={model}
          onChange={(e) => setModel(e.target.value)}
          style={{ padding: "8px" }}
        >
          <option value="gemini-2.5-flash-lite">gemini-2.5-flash-lite</option>
          <option value="gemini-2.5-flash">gemini-2.5-flash</option>
          <option value="gemini-2.0-flash">gemini-2.0-flash</option>
        </select>

        <button
          style={{ padding: "8px 16px" }}
          onClick={sendPrompt}
          disabled={loading}
        >
          {loading ? "Loading..." : "Send"}
        </button>
      </div>

      <table border="1" cellPadding="8" style={{ borderCollapse: "collapse", width: "100%" }}>
        <thead>
          <tr>
            <th>ID</th>
            <th>Model</th>
            <th>Prompt</th>
            <th>Output</th>
            <th>Latency (ms)</th>
            <th>Created</th>
          </tr>
        </thead>
        <tbody>
          {logs.map((r) => (
            <tr key={r.id}>
              <td>{r.id}</td>
              <td>{r.model || DEFAULT_MODEL}</td>
              <td>{r.prompt}</td>
              <td>
                <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                  {r.output}
                </pre>
              </td>
              <td>{r.latency_ms != null ? r.latency_ms.toFixed(2) : ""}</td>
              <td>{fmtCT(r.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default App;
