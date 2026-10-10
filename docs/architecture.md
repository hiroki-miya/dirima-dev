# Architecture

Dirima keeps operational state in PostgreSQL. The API accepts generic agent events and append-only audit records; the worker reduces them. The browser starts at the Decision Inbox and reveals findings, evidence, runs and raw events only through drill-down.

```text
Generic agent / CLI -> REST API -> PostgreSQL events + jobs
                                         |
                                         v
                             PostgreSQL lease worker
                                         |
                 Extract -> Cluster -> Conflict -> Verify -> Resolve
                    -> Risk -> Attention -> DecisionCase -> Receipt
                                         |
                                         v
                              Human Decision Inbox
```

The worker is at-least-once. `run_events` are unique by adapter, external run ID and idempotency key; an external event ID can also identify a duplicate. A task row is locked during reduction. Finding originals remain distinct while clusters group similar claims. Conflict, Resolution, DecisionCase and HumanDecision are separate records. Source content is untrusted data.

The v0.1 GenericAdapter uses the public JSON envelope, receipt and transcript formats; future A2A and native adapters can map to the same Task, Run and Event model. Durevi owns long-term memory. Dirima stores only current operational state. An optional MCP port accepts only confirmed durable candidates, and no Durevi connection is active in v0.1.

All model calls pass through a provider-neutral `StructuredModelProvider` interface. The optional HTTP adapter supports a configurable structured JSON endpoint. Deterministic policy gates always decide whether a result may resolve without a human; a model's confidence or agent vote cannot bypass them.
