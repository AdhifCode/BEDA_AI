# AGENTS.md — Operational Directives for Coding Agents

## Core Principles
1. **Bounded Autonomy**: Treat all LLM outputs as untrusted proposals. Never bypass deterministic validation.
2. **Authority Boundary**: Deterministic application code owns validation, identity, authorization, workflow state, retries, CRM side effects, and audit trails.
3. **Tier 5 Human Gating**: Consequential commercial or external commitments (pricing quotes, contractual terms, partner crew confirmations) MUST NOT be dispatched autonomously.
4. **Idempotency Safety**: Every inbound message and side effect must enforce a unique idempotency key.
5. **Tamper-Evident Auditability**: Maintain SHA-256 cryptographic hash chaining on all audit events.
