import { useState, useEffect } from "react"

interface EnquirySummary {
  enquiry_id: string
  idempotency_key: string
  source_channel: string
  received_at: string
  sender_name: string | null
  sender_email: string | null
  sender_phone: string | null
  subject: string | null
  workflow_status: string
  created_at: string
}

interface ReviewTask {
  id: string
  task_type: string
  reason_code: string
  priority: string
  assigned_to: string | null
  status: string
  created_at: string
}

interface EnquiryDetail {
  enquiry_id: string
  idempotency_key: string
  source_channel: string
  received_at: string
  sender: { name: string | null; email: string | null; phone: string | null }
  subject: string | null
  body_text: string
  workflow_status: string
  ai_understanding: any
  crm_resolution: {
    status: string
    method: string
    selected_customer_id: string | null
    candidates: any[]
    conflict_flags: string[]
  } | null
  draft: {
    id: string
    draft_type: string
    content: string
    grounding_refs: string[]
    requires_approval: boolean
    status: string
  } | null
  review_tasks: ReviewTask[]
  attachments: { id: string; filename: string; content_type: string; sha256: string }[]
  timeline: { step_name: string; status: string; started_at: string; completed_at: string | null }[]
}

interface AuditEvent {
  id: string
  event_type: string
  actor_type: string
  actor_id: string
  timestamp: string
  outcome: string
  metadata: any
  previous_event_hash: string | null
  event_hash: string
}

export default function App() {
  const [enquiries, setEnquiries] = useState<EnquirySummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<EnquiryDetail | null>(null)
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([])
  const [chainValid, setChainValid] = useState<boolean>(true)
  const [filter, setFilter] = useState<string>("ALL")
  const [editedDraft, setEditedDraft] = useState<string>("")
  const [decisionNote, setDecisionNote] = useState<string>("")
  const [, setLoading] = useState<boolean>(false)
  const [message, setMessage] = useState<{ text: string; type: string } | null>(null)

  const fetchEnquiries = async () => {
    try {
      const res = await fetch("/api/v1/enquiries?limit=50")
      if (res.ok) {
        const data = await res.json()
        setEnquiries(data)
        if (data.length > 0 && !selectedId) {
          setSelectedId(data[0].enquiry_id)
        }
      }
    } catch (e) {
      console.error(e)
    }
  }

  const fetchDetail = async (id: string) => {
    setLoading(true)
    try {
      const res = await fetch(`/api/v1/enquiries/${id}`)
      if (res.ok) {
        const data = await res.json()
        setDetail(data)
        if (data.draft) {
          setEditedDraft(data.draft.content)
        }
      }
      const auditRes = await fetch(`/api/v1/enquiries/${id}/audit`)
      if (auditRes.ok) {
        const auditData = await auditRes.json()
        setAuditEvents(auditData.events || [])
        setChainValid(auditData.chain_valid)
      }
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchEnquiries()
  }, [])

  useEffect(() => {
    if (selectedId) {
      fetchDetail(selectedId)
    }
  }, [selectedId])

  const handleDecision = async (reviewId: string, decision: "APPROVE" | "REJECT" | "EDIT_AND_APPROVE") => {
    try {
      const payload: any = {
        decision: decision,
        actor_id: "matt-cooper",
        actor_role: "admin",
        reason: decisionNote || `Decision ${decision} executed via Operations Portal`,
      }
      if (decision === "EDIT_AND_APPROVE") {
        payload.edited_content = editedDraft
      }
      const res = await fetch(`/api/v1/reviews/${reviewId}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
      if (res.ok) {
        setMessage({ text: `Review task successfully resolved: ${decision}`, type: "success" })
        if (selectedId) fetchDetail(selectedId)
        fetchEnquiries()
      } else {
        const err = await res.json()
        setMessage({ text: `Failed: ${err.detail || "Error"}`, type: "danger" })
      }
    } catch (e: any) {
      setMessage({ text: `Network error: ${e.message}`, type: "danger" })
    }
  }

  const handleRetry = async (id: string) => {
    try {
      const res = await fetch(`/api/v1/enquiries/${id}/retry`, { method: "POST" })
      if (res.ok) {
        setMessage({ text: "Workflow retry dispatched successfully!", type: "success" })
        fetchDetail(id)
        fetchEnquiries()
      }
    } catch (e: any) {
      setMessage({ text: e.message, type: "danger" })
    }
  }

  const filteredEnquiries = enquiries.filter(e => {
    if (filter === "ALL") return true
    if (filter === "APPROVAL") return e.workflow_status === "APPROVAL"
    if (filter === "CRM_REVIEW") return e.workflow_status === "CRM_REVIEW"
    if (filter === "COMPLETED") return e.workflow_status === "COMPLETED"
    if (filter === "QUARANTINED") return e.workflow_status === "QUARANTINED"
    return true
  })

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      {/* Header */}
      <header style={{ backgroundColor: "#020617", borderBottom: "1px solid #1e293b", padding: "1rem 2rem", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          <div style={{ width: "32px", height: "32px", backgroundColor: "#2563eb", borderRadius: "8px", display: "flex", alignItems: "center", justifyContent: "center", fontWeight: "bold", fontSize: "1.2rem" }}>B</div>
          <div>
            <h1 style={{ fontSize: "1.125rem", fontWeight: 700, letterSpacing: "-0.025em" }}>BEDA Enquiry Intelligence & Review Portal</h1>
            <p style={{ fontSize: "0.75rem", color: "#94a3b8" }}>Bounded-Autonomy CRM Operations Engine</p>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          <span style={{ fontSize: "0.75rem", color: "#64748b" }}>User: Matt Cooper (Founder / Admin)</span>
          <button className="btn btn-secondary" onClick={fetchEnquiries} style={{ fontSize: "0.75rem", padding: "0.35rem 0.75rem" }}>↻ Refresh Queue</button>
        </div>
      </header>

      {/* Alert toast */}
      {message && (
        <div style={{ backgroundColor: message.type === "success" ? "#064e3b" : "#7f1d1d", color: "#fff", padding: "0.5rem 2rem", fontSize: "0.875rem", display: "flex", justifyContent: "space-between" }}>
          <span>{message.text}</span>
          <button onClick={() => setMessage(null)} style={{ background: "none", border: "none", color: "#fff", cursor: "pointer" }}>✕</button>
        </div>
      )}

      {/* Main split view */}
      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        {/* Sidebar Queue */}
        <div style={{ width: "380px", borderRight: "1px solid #1e293b", backgroundColor: "#090d16", display: "flex", flexDirection: "column" }}>
          <div style={{ padding: "0.75rem 1rem", borderBottom: "1px solid #1e293b", display: "flex", gap: "0.5rem", overflowX: "auto" }}>
            {["ALL", "APPROVAL", "CRM_REVIEW", "COMPLETED", "QUARANTINED"].map(t => (
              <button
                key={t}
                onClick={() => setFilter(t)}
                style={{
                  padding: "0.25rem 0.5rem",
                  fontSize: "0.7rem",
                  borderRadius: "4px",
                  border: "none",
                  cursor: "pointer",
                  backgroundColor: filter === t ? "#2563eb" : "#1e293b",
                  color: filter === t ? "#fff" : "#94a3b8",
                  fontWeight: 600,
                }}
              >
                {t.replace("_", " ")}
              </button>
            ))}
          </div>

          <div style={{ flex: 1, overflowY: "auto" }}>
            {filteredEnquiries.map(e => (
              <div
                key={e.enquiry_id}
                onClick={() => setSelectedId(e.enquiry_id)}
                style={{
                  padding: "0.875rem 1rem",
                  borderBottom: "1px solid #1e293b",
                  cursor: "pointer",
                  backgroundColor: selectedId === e.enquiry_id ? "#1e293b" : "transparent",
                  transition: "background-color 0.15s",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "0.25rem" }}>
                  <span style={{ fontSize: "0.75rem", fontFamily: "JetBrains Mono", color: "#38bdf8" }}>{e.source_channel.toUpperCase()}</span>
                  <span className={`badge badge-${e.workflow_status.toLowerCase()}`}>{e.workflow_status}</span>
                </div>
                <div style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "0.25rem", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {e.subject || "(No Subject)"}
                </div>
                <div style={{ fontSize: "0.75rem", color: "#94a3b8", display: "flex", justifyContent: "space-between" }}>
                  <span>{e.sender_name || e.sender_email || "Unknown"}</span>
                  <span>{new Date(e.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Right Detail Pane */}
        <div style={{ flex: 1, overflowY: "auto", padding: "1.5rem 2rem", backgroundColor: "#0f172a" }}>
          {detail ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem", maxWidth: "1200px", margin: "0 auto" }}>
              {/* Header banner */}
              <div className="panel" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "0.5rem" }}>
                    <h2 style={{ fontSize: "1.25rem", fontWeight: 700 }}>{detail.subject || "(No Subject)"}</h2>
                    <span className={`badge badge-${detail.workflow_status.toLowerCase()}`}>{detail.workflow_status}</span>
                  </div>
                  <div style={{ fontSize: "0.75rem", color: "#94a3b8", display: "flex", gap: "1.5rem" }}>
                    <span>Enquiry ID: <code style={{ color: "#38bdf8" }}>{detail.enquiry_id}</code></span>
                    <span>Channel: <strong>{detail.source_channel}</strong></span>
                    <span>Received: <strong>{new Date(detail.received_at).toLocaleString()}</strong></span>
                  </div>
                </div>
                <button className="btn btn-secondary" onClick={() => handleRetry(detail.enquiry_id)}>↻ Retry Workflow</button>
              </div>

              {/* Sender & Message Details */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1.5rem" }}>
                <div className="panel">
                  <h3 style={{ fontSize: "0.875rem", fontWeight: 600, color: "#94a3b8", textTransform: "uppercase", marginBottom: "0.75rem" }}>Sender Information</h3>
                  <div style={{ fontSize: "0.875rem", display: "flex", flexDirection: "column", gap: "0.4rem" }}>
                    <div>Name: <strong>{detail.sender.name || "N/A"}</strong></div>
                    <div>Email: <strong>{detail.sender.email || "N/A"}</strong></div>
                    <div>Phone: <strong>{detail.sender.phone || "N/A"}</strong></div>
                    {detail.attachments.length > 0 && (
                      <div style={{ marginTop: "0.5rem" }}>
                        <span style={{ color: "#94a3b8", fontSize: "0.75rem" }}>Attachments:</span>
                        <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.25rem" }}>
                          {detail.attachments.map(a => (
                            <span key={a.id} style={{ backgroundColor: "#334155", padding: "0.2rem 0.5rem", borderRadius: "4px", fontSize: "0.75rem" }}>📎 {a.filename}</span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>

                <div className="panel">
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
                    <h3 style={{ fontSize: "0.875rem", fontWeight: 600, color: "#94a3b8", textTransform: "uppercase", margin: 0 }}>AI Understanding & Extraction</h3>
                    {detail.ai_understanding?.provider && (
                      <span style={{
                        fontSize: "0.7rem",
                        padding: "0.2rem 0.5rem",
                        borderRadius: "9999px",
                        fontWeight: 600,
                        backgroundColor: detail.ai_understanding.provider === "fake" ? "#1e293b" : "#0c4a6e",
                        color: detail.ai_understanding.provider === "fake" ? "#94a3b8" : "#38bdf8",
                        border: `1px solid ${detail.ai_understanding.provider === "fake" ? "#334155" : "#0284c7"}`
                      }}>
                        {detail.ai_understanding.provider === "fake" ? "STATIC (FAKE)" : "LIVE LLM"}: {detail.ai_understanding.model || "default"}
                        {detail.ai_understanding.latency_ms > 0 ? ` (${detail.ai_understanding.latency_ms}ms)` : ""}
                      </span>
                    )}
                  </div>
                  {detail.ai_understanding ? (
                    <div style={{ fontSize: "0.875rem", display: "flex", flexDirection: "column", gap: "0.4rem" }}>
                      {detail.ai_understanding.error_code && (
                        <div style={{ color: "#f87171", fontSize: "0.75rem", backgroundColor: "#450a0a", padding: "0.25rem 0.5rem", borderRadius: "0.25rem", border: "1px solid #7f1d1d" }}>
                          Fallback Notice: {detail.ai_understanding.error_code}
                        </div>
                      )}
                      <div>Category: <strong style={{ color: "#38bdf8" }}>{detail.ai_understanding.classification?.category}</strong> (Confidence: {(detail.ai_understanding.classification?.confidence * 100).toFixed(0)}%)</div>
                      <div>Intent: <span>{detail.ai_understanding.extracted?.intent_summary}</span></div>
                      {detail.ai_understanding.extracted?.discrepancy_amount && (
                        <div style={{ color: "#fbbf24", fontWeight: 600 }}>Invoice Discrepancy: {detail.ai_understanding.extracted.discrepancy_amount}</div>
                      )}
                      {detail.ai_understanding.extracted?.key_constraints?.length > 0 && (
                        <div style={{ color: "#f87171", fontSize: "0.75rem", marginTop: "0.25rem" }}>
                          Key Constraints: {detail.ai_understanding.extracted.key_constraints.join("; ")}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div style={{ color: "#64748b" }}>AI analysis pending or in progress...</div>
                  )}
                </div>
              </div>

              {/* Raw Message Body */}
              <div className="panel">
                <h3 style={{ fontSize: "0.875rem", fontWeight: 600, color: "#94a3b8", textTransform: "uppercase", marginBottom: "0.75rem" }}>Customer Message (Untrusted Input)</h3>
                <div style={{ backgroundColor: "#020617", padding: "1rem", borderRadius: "0.375rem", fontSize: "0.875rem", whiteSpace: "pre-wrap", border: "1px solid #1e293b", fontFamily: "Inter, sans-serif" }}>
                  {detail.body_text}
                </div>
              </div>

              {/* CRM Identity Resolution Card */}
              {detail.crm_resolution && (
                <div className="panel" style={{ borderLeft: detail.crm_resolution.status === "MATCHED" ? "4px solid #10b981" : detail.crm_resolution.status === "AMBIGUOUS" ? "4px solid #f59e0b" : "4px solid #3b82f6" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
                    <h3 style={{ fontSize: "0.875rem", fontWeight: 600, color: "#94a3b8", textTransform: "uppercase" }}>CRM Identity Resolution</h3>
                    <span className={`badge badge-${detail.crm_resolution.status.toLowerCase()}`}>{detail.crm_resolution.status} via {detail.crm_resolution.method}</span>
                  </div>

                  {detail.crm_resolution.conflict_flags.length > 0 && (
                    <div style={{ backgroundColor: "#451a03", border: "1px solid #78350f", color: "#fde68a", padding: "0.75rem", borderRadius: "0.375rem", marginBottom: "1rem", fontSize: "0.875rem" }}>
                      ⚠️ <strong>Conflict Warning:</strong> {detail.crm_resolution.conflict_flags.join(" | ")}
                    </div>
                  )}

                  {detail.crm_resolution.candidates.length > 0 ? (
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "1rem" }}>
                      {detail.crm_resolution.candidates.map((c: any) => (
                        <div key={c.customer_id} style={{ backgroundColor: "#020617", border: "1px solid #334155", padding: "0.75rem", borderRadius: "0.375rem" }}>
                          <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 600, color: "#38bdf8" }}>
                            <span>{c.customer_id}: {c.company_name}</span>
                            <span>Score: {(c.match_score * 100).toFixed(0)}%</span>
                          </div>
                          <div style={{ fontSize: "0.75rem", color: "#94a3b8", marginTop: "0.25rem" }}>
                            Contact: {c.contact_name} | Email: {c.email || "N/A"} | Phone: {c.phone || "N/A"}
                          </div>
                          <div style={{ marginTop: "0.5rem", display: "flex", gap: "0.25rem", flexWrap: "wrap" }}>
                            {c.match_reasons?.map((r: string, idx: number) => (
                              <span key={idx} style={{ backgroundColor: "#1e293b", fontSize: "0.7rem", padding: "0.15rem 0.4rem", borderRadius: "3px" }}>{r}</span>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div style={{ fontSize: "0.875rem", color: "#94a3b8" }}>No existing CRM match. Ready to create new customer record upon approval.</div>
                  )}
                </div>
              )}

              {/* Grounded Response / Approval Card */}
              {detail.draft && (
                <div className="panel" style={{ borderLeft: "4px solid #6366f1" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
                    <div>
                      <h3 style={{ fontSize: "0.875rem", fontWeight: 600, color: "#94a3b8", textTransform: "uppercase" }}>
                        Grounded Draft Response ({detail.draft.draft_type.toUpperCase()})
                      </h3>
                      <span style={{ fontSize: "0.75rem", color: "#a5b4fc" }}>Tier 5 Bounded Autonomy Guard: Requires Human Approval</span>
                    </div>
                    <span className="badge badge-approval">{detail.draft.status}</span>
                  </div>

                  {/* Grounding references */}
                  {detail.draft.grounding_refs.length > 0 && (
                    <div style={{ marginBottom: "0.75rem", display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                      <span style={{ fontSize: "0.75rem", color: "#94a3b8" }}>Evidence:</span>
                      {detail.draft.grounding_refs.map((ref: string, idx: number) => (
                        <span key={idx} style={{ backgroundColor: "#1e1b4b", border: "1px solid #312e81", color: "#c7d2fe", fontSize: "0.7rem", padding: "0.15rem 0.4rem", borderRadius: "3px" }}>
                          ✓ {ref}
                        </span>
                      ))}
                    </div>
                  )}

                  {/* Draft content editor */}
                  <textarea
                    value={editedDraft}
                    onChange={e => setEditedDraft(e.target.value)}
                    rows={7}
                    style={{
                      width: "100%",
                      backgroundColor: "#020617",
                      border: "1px solid #334155",
                      color: "#f8fafc",
                      padding: "0.75rem",
                      borderRadius: "0.375rem",
                      fontFamily: "Inter, sans-serif",
                      fontSize: "0.875rem",
                      marginBottom: "1rem",
                      resize: "vertical",
                    }}
                  />

                  {/* Review tasks & Decision Controls */}
                  {detail.review_tasks.filter(t => t.status === "OPEN").map(t => (
                    <div key={t.id} style={{ backgroundColor: "#020617", border: "1px solid #1e293b", padding: "1rem", borderRadius: "0.375rem" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
                        <div>
                          <strong>Task: {t.task_type}</strong> — Reason: <span style={{ color: "#fb923c" }}>{t.reason_code}</span>
                          <div style={{ fontSize: "0.75rem", color: "#94a3b8" }}>Assigned Owner: {t.assigned_to || "Unassigned / Engineering Review"}</div>
                        </div>
                        <span className="badge badge-approval">{t.priority}</span>
                      </div>

                      <div style={{ marginBottom: "0.75rem" }}>
                        <input
                          type="text"
                          placeholder="Approval notes / audit reason (e.g. Verified and approved for dispatch)"
                          value={decisionNote}
                          onChange={e => setDecisionNote(e.target.value)}
                          style={{ width: "100%", padding: "0.5rem", backgroundColor: "#0f172a", border: "1px solid #334155", color: "#fff", borderRadius: "4px", fontSize: "0.8rem" }}
                        />
                      </div>

                      <div style={{ display: "flex", gap: "0.75rem" }}>
                        <button className="btn btn-success" onClick={() => handleDecision(t.id, "APPROVE")}>✓ Approve & Dispatch</button>
                        <button className="btn btn-primary" onClick={() => handleDecision(t.id, "EDIT_AND_APPROVE")}>✎ Edit & Approve</button>
                        <button className="btn btn-danger" onClick={() => handleDecision(t.id, "REJECT")}>✕ Reject</button>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Cryptographic Audit Trail */}
              <div className="panel">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
                  <h3 style={{ fontSize: "0.875rem", fontWeight: 600, color: "#94a3b8", textTransform: "uppercase" }}>
                    Cryptographic Audit Trail ({auditEvents.length} Events)
                  </h3>
                  {chainValid ? (
                    <span style={{ color: "#34d399", fontSize: "0.75rem", fontWeight: 600, backgroundColor: "#064e3b", padding: "0.2rem 0.5rem", borderRadius: "4px" }}>
                      🔒 SHA-256 CHAIN VERIFIED
                    </span>
                  ) : (
                    <span style={{ color: "#f87171", fontSize: "0.75rem", fontWeight: 600, backgroundColor: "#7f1d1d", padding: "0.2rem 0.5rem", borderRadius: "4px" }}>
                      ⚠️ TAMPER WARNING
                    </span>
                  )}
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                  {auditEvents.map(ev => (
                    <div key={ev.id} style={{ backgroundColor: "#020617", border: "1px solid #1e293b", padding: "0.5rem 0.75rem", borderRadius: "4px", fontSize: "0.75rem", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <div>
                        <span style={{ color: "#38bdf8", fontWeight: 600 }}>{ev.event_type}</span>
                        <span style={{ color: "#94a3b8", marginLeft: "0.5rem" }}>[{ev.actor_type}: {ev.actor_id}]</span>
                        <span style={{ color: "#64748b", marginLeft: "0.5rem" }}>→ {ev.outcome}</span>
                      </div>
                      <div style={{ fontFamily: "JetBrains Mono", color: "#64748b", fontSize: "0.7rem" }}>
                        hash: {ev.event_hash.slice(0, 12)}...
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div style={{ textAlign: "center", padding: "4rem", color: "#64748b" }}>Select an enquiry from the queue to view details and action reviews.</div>
          )}
        </div>
      </div>
    </div>
  )
}
