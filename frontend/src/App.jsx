import { useState, useEffect, useCallback } from "react";
import { CheckCircle, AlertTriangle, Clock, Lock, Upload, RefreshCw, ChevronDown, Filter, X, FileText, LogOut } from "lucide-react";

const API = "/api";

const SCOPE_COLORS = {
  scope_1: { bg: "bg-orange-100", text: "text-orange-800", label: "Scope 1", dot: "bg-orange-500" },
  scope_2: { bg: "bg-blue-100", text: "text-blue-800", label: "Scope 2", dot: "bg-blue-500" },
  scope_3: { bg: "bg-purple-100", text: "text-purple-800", label: "Scope 3", dot: "bg-purple-500" },
};
const STATUS_CONFIG = {
  pending: { icon: Clock, color: "text-gray-500", bg: "bg-gray-100", label: "Pending" },
  flagged: { icon: AlertTriangle, color: "text-amber-600", bg: "bg-amber-50", label: "Flagged" },
  approved: { icon: CheckCircle, color: "text-green-600", bg: "bg-green-50", label: "Approved" },
  locked: { icon: Lock, color: "text-blue-600", bg: "bg-blue-50", label: "Locked" },
};
const FLAG_SEVERITY_COLORS = {
  error: "bg-red-100 text-red-800 border-red-200",
  warning: "bg-amber-100 text-amber-800 border-amber-200",
  info: "bg-blue-100 text-blue-700 border-blue-200",
};

function fmt(n) {
  if (n == null) return "—";
  const num = parseFloat(n);
  if (num >= 1_000_000) return `${(num/1_000_000).toFixed(2)}M`;
  if (num >= 1_000) return `${(num/1_000).toFixed(1)}k`;
  return num.toFixed(2);
}
function fmtCO2(kg) {
  if (kg == null) return "—";
  const t = parseFloat(kg)/1000;
  return t >= 1 ? `${t.toFixed(2)} tCO₂e` : `${parseFloat(kg).toFixed(1)} kgCO₂e`;
}

async function apiFetch(path, opts = {}, token) {
  const res = await fetch(`${API}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Token ${token}` } : {}),
      ...opts.headers,
    },
    ...opts,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json();
}

// ── Login Screen ─────────────────────────────────────────────────────────────
function LoginScreen({ onLogin }) {
  const [username, setUsername] = useState("analyst");
  const [password, setPassword] = useState("breathe-analyst-2024");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function handleLogin(e) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API}/auth/token/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!res.ok) throw new Error("Invalid credentials");
      const data = await res.json();
      onLogin(data.token);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-lg w-full max-w-sm p-8">
        <div className="flex items-center gap-3 mb-8">
          <div className="w-10 h-10 bg-emerald-600 rounded-xl flex items-center justify-center">
            <span className="text-white font-bold text-lg">B</span>
          </div>
          <div>
            <h1 className="text-lg font-bold text-gray-900">Breathe ESG</h1>
            <p className="text-xs text-gray-500">Emissions Platform</p>
          </div>
        </div>
        <form onSubmit={handleLogin} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Username</label>
            <input
              type="text" value={username} onChange={e => setUsername(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Password</label>
            <input
              type="password" value={password} onChange={e => setPassword(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
            />
          </div>
          {error && <p className="text-sm text-red-600 bg-red-50 rounded-lg p-2">{error}</p>}
          <button type="submit" disabled={loading}
            className="w-full py-2 bg-emerald-600 text-white text-sm font-medium rounded-lg hover:bg-emerald-700 disabled:opacity-50 flex items-center justify-center gap-2">
            {loading ? <RefreshCw size={14} className="animate-spin" /> : null}
            {loading ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <div className="mt-6 p-3 bg-gray-50 rounded-lg text-xs text-gray-500 space-y-1">
          <p className="font-medium text-gray-600">Demo accounts:</p>
          <p>analyst / breathe-analyst-2024</p>
          <p>admin / breathe-admin-2024</p>
          <p>auditor / breathe-auditor-2024</p>
        </div>
      </div>
    </div>
  );
}

function ScopeBar({ scope1, scope2, scope3 }) {
  const total = scope1 + scope2 + scope3 || 1;
  return (
    <div className="flex rounded-full overflow-hidden h-2 w-full">
      <div className="bg-orange-500 transition-all" style={{ width: `${(scope1/total)*100}%` }} />
      <div className="bg-blue-500 transition-all" style={{ width: `${(scope2/total)*100}%` }} />
      <div className="bg-purple-500 transition-all" style={{ width: `${(scope3/total)*100}%` }} />
    </div>
  );
}
function StatusBadge({ status }) {
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.pending;
  const Icon = cfg.icon;
  return (
    <span className={`inline-flex items-center gap-1 text-xs px-2 py-1 rounded-full font-medium ${cfg.bg} ${cfg.color}`}>
      <Icon size={11} />{cfg.label}
    </span>
  );
}
function ScopeBadge({ scope }) {
  const cfg = SCOPE_COLORS[scope];
  if (!cfg) return null;
  return (
    <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium ${cfg.bg} ${cfg.text}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot}`} />{cfg.label}
    </span>
  );
}

function UploadModal({ onClose, onSuccess, token }) {
  const [sourceType, setSourceType] = useState("sap");
  const [file, setFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const sources = [
    { value: "sap", label: "SAP Fuel & Procurement", hint: "MM60/ME2M flat file (.txt, .csv)" },
    { value: "utility", label: "Utility Electricity", hint: "Portal CSV export (.csv)" },
    { value: "travel", label: "Corporate Travel", hint: "Concur SAE or Navan CSV (.csv)" },
  ];
  async function handleSubmit() {
    if (!file) return;
    setLoading(true); setError(null);
    try {
      const fd = new FormData();
      fd.append("source_type", sourceType);
      fd.append("file", file);
      const res = await fetch(`${API}/jobs/upload/`, {
        method: "POST",
        headers: token ? { Authorization: `Token ${token}` } : {},
        body: fd,
      });
      if (!res.ok) throw new Error(await res.text());
      onSuccess(await res.json());
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md">
        <div className="flex items-center justify-between p-6 border-b">
          <h2 className="text-lg font-semibold">Upload Data</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600"><X size={20}/></button>
        </div>
        <div className="p-6 space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Data source</label>
            <div className="space-y-2">
              {sources.map(s => (
                <label key={s.value} className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${sourceType===s.value?"border-blue-500 bg-blue-50":"border-gray-200 hover:border-gray-300"}`}>
                  <input type="radio" name="source" value={s.value} checked={sourceType===s.value} onChange={()=>setSourceType(s.value)} className="mt-0.5" />
                  <div><p className="text-sm font-medium">{s.label}</p><p className="text-xs text-gray-500">{s.hint}</p></div>
                </label>
              ))}
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">File</label>
            <div className={`border-2 border-dashed rounded-lg p-6 text-center cursor-pointer ${file?"border-green-400 bg-green-50":"border-gray-300 hover:border-gray-400"}`}
              onClick={()=>document.getElementById("file-input").click()}>
              {file ? (
                <div><FileText className="mx-auto text-green-500 mb-2" size={24}/>
                  <p className="text-sm font-medium">{file.name}</p>
                  <p className="text-xs text-gray-500">{(file.size/1024).toFixed(1)} KB</p>
                </div>
              ) : (
                <div><Upload className="mx-auto text-gray-400 mb-2" size={24}/>
                  <p className="text-sm text-gray-600">Drop a file or click to browse</p>
                </div>
              )}
              <input id="file-input" type="file" className="hidden" accept=".csv,.txt,.tsv" onChange={e=>setFile(e.target.files[0])}/>
            </div>
          </div>
          {error && <p className="text-sm text-red-600 bg-red-50 rounded-lg p-3">{error}</p>}
        </div>
        <div className="p-6 pt-0 flex gap-3">
          <button onClick={onClose} className="flex-1 px-4 py-2 text-sm font-medium text-gray-700 border border-gray-300 rounded-lg hover:bg-gray-50">Cancel</button>
          <button onClick={handleSubmit} disabled={!file||loading}
            className="flex-1 px-4 py-2 text-sm font-medium text-white bg-emerald-600 rounded-lg hover:bg-emerald-700 disabled:opacity-50 flex items-center justify-center gap-2">
            {loading?<RefreshCw size={14} className="animate-spin"/>:<Upload size={14}/>}
            {loading?"Processing…":"Upload & Process"}
          </button>
        </div>
      </div>
    </div>
  );
}

function RecordRow({ record, onApprove, onFlag, selected, onSelect }) {
  const [expanded, setExpanded] = useState(false);
  const hasFlags = record.flags && record.flags.length > 0;
  return (
    <>
      <tr className={`border-b transition-colors ${selected?"bg-blue-50":hasFlags?"bg-amber-50/40 hover:bg-amber-50":"hover:bg-gray-50"}`}>
        <td className="px-4 py-3"><input type="checkbox" checked={selected} onChange={()=>onSelect(record.id)} className="rounded"/></td>
        <td className="px-4 py-3">
          <button onClick={()=>setExpanded(!expanded)} className="text-gray-400 hover:text-gray-600">
            <ChevronDown size={14} className={`transition-transform ${expanded?"rotate-180":""}`}/>
          </button>
        </td>
        <td className="px-4 py-3">
          <p className="text-sm font-medium text-gray-900 max-w-xs truncate">{record.activity_description}</p>
          <p className="text-xs text-gray-400">{record.activity_date}</p>
        </td>
        <td className="px-4 py-3"><ScopeBadge scope={record.scope}/></td>
        <td className="px-4 py-3 text-sm font-mono text-gray-700">{fmtCO2(record.co2e_kg)}</td>
        <td className="px-4 py-3">
          {hasFlags && <span className="inline-flex items-center gap-1 text-xs text-amber-600"><AlertTriangle size={12}/>{record.flags.length}</span>}
        </td>
        <td className="px-4 py-3"><StatusBadge status={record.status}/></td>
        <td className="px-4 py-3">
          <div className="flex gap-2">
            {record.status!=="approved"&&record.status!=="locked"&&(
              <button onClick={()=>onApprove(record.id)} className="text-xs px-2 py-1 bg-green-100 text-green-700 rounded hover:bg-green-200 font-medium">Approve</button>
            )}
            {record.status!=="locked"&&(
              <button onClick={()=>onFlag(record.id)} className="text-xs px-2 py-1 bg-amber-100 text-amber-700 rounded hover:bg-amber-200 font-medium">Flag</button>
            )}
          </div>
        </td>
      </tr>
      {expanded && (
        <tr className="bg-gray-50 border-b">
          <td colSpan={8} className="px-8 py-4">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm mb-3">
              <div><p className="text-xs font-medium text-gray-500 mb-1">Source</p><p className="font-mono text-gray-700 uppercase">{record.source_type}</p></div>
              <div><p className="text-xs font-medium text-gray-500 mb-1">Raw qty</p><p className="font-mono text-gray-700">{fmt(record.quantity_raw)} {record.unit_raw}</p></div>
              <div><p className="text-xs font-medium text-gray-500 mb-1">Normalized</p><p className="font-mono text-gray-700">{fmt(record.quantity_normalized)} {record.unit_normalized}</p></div>
              <div><p className="text-xs font-medium text-gray-500 mb-1">Period</p><p className="font-mono text-gray-700">{record.period_start} → {record.period_end}</p></div>
            </div>
            {record.flags?.length>0 && (
              <div className="space-y-1.5">
                <p className="text-xs font-medium text-gray-500">Flags</p>
                {record.flags.map((f,i) => (
                  <div key={i} className={`flex items-start gap-2 p-2 rounded text-xs border ${FLAG_SEVERITY_COLORS[f.severity]||FLAG_SEVERITY_COLORS.info}`}>
                    <span className="font-mono font-bold shrink-0">{f.code}</span>
                    <span>{f.message}</span>
                  </div>
                ))}
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

export default function App() {
  const [token, setToken] = useState(() => localStorage.getItem("esg_token") || "");
  const [summary, setSummary] = useState(null);
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showUpload, setShowUpload] = useState(false);
  const [filters, setFilters] = useState({ status:"", scope:"", source_type:"" });
  const [selected, setSelected] = useState(new Set());
  const [page, setPage] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [activeTab, setActiveTab] = useState("records");

  function handleLogin(t) {
    localStorage.setItem("esg_token", t);
    setToken(t);
  }
  function handleLogout() {
    localStorage.removeItem("esg_token");
    setToken("");
  }

  const loadSummary = useCallback(async () => {
    if (!token) return;
    try { setSummary(await apiFetch("/dashboard/summary/", {}, token)); }
    catch(e) { if (e.message.startsWith("401")) setToken(""); }
  }, [token]);

  const loadRecords = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      const params = new URLSearchParams({ page, page_size:25, ...Object.fromEntries(Object.entries(filters).filter(([,v])=>v)) });
      const data = await apiFetch(`/records/?${params}`, {}, token);
      setRecords(data.results || data);
      setTotalCount(data.count || (data.results||data).length);
    } catch(e) { console.error(e); }
    finally { setLoading(false); }
  }, [token, filters, page]);

  useEffect(() => { loadSummary(); loadRecords(); }, [loadSummary, loadRecords]);

  async function handleApprove(id) {
    await apiFetch(`/records/${id}/approve/`, {method:"POST",body:JSON.stringify({})}, token);
    loadRecords(); loadSummary();
  }
  async function handleFlag(id) {
    const msg = prompt("Reason (optional):") || "Flagged by analyst";
    await apiFetch(`/records/${id}/flag/`, {method:"POST",body:JSON.stringify({message:msg})}, token);
    loadRecords();
  }
  async function handleBulkApprove() {
    if (!selected.size) return;
    await apiFetch("/records/bulk_approve/", {method:"POST",body:JSON.stringify({ids:[...selected]})}, token);
    setSelected(new Set()); loadRecords(); loadSummary();
  }
  function toggleSelect(id) { setSelected(prev=>{ const n=new Set(prev); n.has(id)?n.delete(id):n.add(id); return n; }); }
  function toggleSelectAll() { setSelected(prev=>prev.size===records.length?new Set():new Set(records.map(r=>r.id))); }

  if (!token) return <LoginScreen onLogin={handleLogin}/>;

  const s1 = parseFloat(summary?.totals?.scope_1_co2e_kg||0)/1000;
  const s2 = parseFloat(summary?.totals?.scope_2_co2e_kg||0)/1000;
  const s3 = parseFloat(summary?.totals?.scope_3_co2e_kg||0)/1000;

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 bg-emerald-600 rounded-lg flex items-center justify-center">
              <span className="text-white font-bold text-sm">B</span>
            </div>
            <div>
              <h1 className="text-base font-semibold text-gray-900">Breathe ESG</h1>
              <p className="text-xs text-gray-500">Emissions Ingestion Platform</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button onClick={()=>setShowUpload(true)}
              className="flex items-center gap-2 px-4 py-2 bg-emerald-600 text-white text-sm font-medium rounded-lg hover:bg-emerald-700">
              <Upload size={15}/>Upload Data
            </button>
            <button onClick={handleLogout} className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700">
              <LogOut size={15}/>Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8 space-y-6">
        {summary && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="bg-white rounded-xl border border-gray-200 p-5 col-span-2">
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-3">Total Approved Emissions</p>
              <p className="text-3xl font-bold text-gray-900">{(s1+s2+s3).toFixed(1)} <span className="text-lg font-normal text-gray-400">tCO₂e</span></p>
              <div className="mt-3 space-y-1.5">
                <ScopeBar scope1={s1} scope2={s2} scope3={s3}/>
                <div className="flex justify-between text-xs text-gray-500">
                  <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-orange-500 inline-block"/>S1: {s1.toFixed(1)}t</span>
                  <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-blue-500 inline-block"/>S2: {s2.toFixed(1)}t</span>
                  <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-purple-500 inline-block"/>S3: {s3.toFixed(1)}t</span>
                </div>
              </div>
            </div>
            <div className="bg-white rounded-xl border p-5">
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">Pending Review</p>
              <p className="text-2xl font-bold text-gray-700">{summary.review_queue.pending}</p>
              <p className="text-xs text-amber-600 mt-1">{summary.review_queue.flagged} flagged</p>
            </div>
            <div className="bg-white rounded-xl border p-5">
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">Approved</p>
              <p className="text-2xl font-bold text-green-600">{summary.review_queue.approved}</p>
              <p className="text-xs text-blue-600 mt-1">{summary.review_queue.locked} locked</p>
            </div>
          </div>
        )}

        <div className="flex gap-1 border-b border-gray-200">
          {["records","jobs"].map(tab=>(
            <button key={tab} onClick={()=>setActiveTab(tab)}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors capitalize ${activeTab===tab?"border-emerald-600 text-emerald-700":"border-transparent text-gray-500 hover:text-gray-700"}`}>
              {tab==="records"?"Emissions Records":"Ingestion Jobs"}
            </button>
          ))}
        </div>

        {activeTab==="records" && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex items-center gap-2 text-sm text-gray-500"><Filter size={14}/><span className="font-medium">Filter:</span></div>
              {[
                {key:"status", options:[["","All status"],["pending","Pending"],["flagged","Flagged"],["approved","Approved"]]},
                {key:"scope", options:[["","All scopes"],["scope_1","Scope 1"],["scope_2","Scope 2"],["scope_3","Scope 3"]]},
                {key:"source_type", options:[["","All sources"],["sap","SAP"],["utility","Utility"],["travel","Travel"]]},
              ].map(({key,options})=>(
                <select key={key} value={filters[key]}
                  onChange={e=>{setFilters(f=>({...f,[key]:e.target.value}));setPage(1);}}
                  className="text-sm border border-gray-200 rounded-lg px-3 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-emerald-500">
                  {options.map(([v,l])=><option key={v} value={v}>{l}</option>)}
                </select>
              ))}
              {selected.size>0 && (
                <button onClick={handleBulkApprove}
                  className="ml-auto flex items-center gap-2 px-4 py-1.5 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700">
                  <CheckCircle size={14}/>Approve {selected.size} selected
                </button>
              )}
            </div>

            <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b bg-gray-50">
                    <th className="px-4 py-3"><input type="checkbox" onChange={toggleSelectAll} checked={selected.size===records.length&&records.length>0} className="rounded"/></th>
                    <th className="w-8"/>
                    {["Activity","Scope","CO₂e","Flags","Status","Actions"].map(h=>(
                      <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {loading ? (
                    <tr><td colSpan={8} className="text-center py-16 text-gray-400">
                      <RefreshCw size={20} className="animate-spin mx-auto mb-2"/>Loading records…
                    </td></tr>
                  ) : records.length===0 ? (
                    <tr><td colSpan={8} className="text-center py-16 text-gray-400">No records found.</td></tr>
                  ) : records.map(r=>(
                    <RecordRow key={r.id} record={r} onApprove={handleApprove} onFlag={handleFlag} selected={selected.has(r.id)} onSelect={toggleSelect}/>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="flex items-center justify-between text-sm text-gray-500">
              <span>{totalCount} total records</span>
              <div className="flex gap-2">
                <button onClick={()=>setPage(p=>Math.max(1,p-1))} disabled={page===1} className="px-3 py-1 border rounded-lg hover:bg-gray-50 disabled:opacity-40">← Prev</button>
                <span className="px-3 py-1 bg-white border rounded-lg">Page {page}</span>
                <button onClick={()=>setPage(p=>p+1)} disabled={records.length<25} className="px-3 py-1 border rounded-lg hover:bg-gray-50 disabled:opacity-40">Next →</button>
              </div>
            </div>
          </div>
        )}

        {activeTab==="jobs" && summary && (
          <div className="bg-white rounded-xl border overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-gray-50">
                  {["Source","Status","Total","OK","Failed","Flagged","Date"].map(h=>(
                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {summary.recent_jobs.map(job=>(
                  <tr key={job.id} className="border-b hover:bg-gray-50">
                    <td className="px-4 py-3 font-medium capitalize">{job.source_type}</td>
                    <td className="px-4 py-3"><StatusBadge status={job.status}/></td>
                    <td className="px-4 py-3 font-mono">{job.rows_total}</td>
                    <td className="px-4 py-3 text-green-600 font-mono">{job.rows_success}</td>
                    <td className="px-4 py-3 text-red-600 font-mono">{job.rows_failed}</td>
                    <td className="px-4 py-3 text-amber-600 font-mono">{job.rows_flagged}</td>
                    <td className="px-4 py-3 text-gray-400 text-xs">{new Date(job.created_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </main>

      {showUpload && <UploadModal token={token} onClose={()=>setShowUpload(false)} onSuccess={()=>{setShowUpload(false);loadRecords();loadSummary();}}/>}
    </div>
  );
}
