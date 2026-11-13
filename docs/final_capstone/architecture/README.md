# Final Architecture Diagrams

These diagrams describe the system **as implemented** at the final evaluation commit (`7a1a875`)
plus the Milestone 7 documentation changes. Anything not implemented is drawn in grey and labelled
"future". The Tree of Thought (ToT) is a **conditional subgraph** run by the Orchestrator. It is
not a fifth agent.

| File | What it is | How to edit |
|---|---|---|
| [final_architecture.png](final_architecture.png) | Presentation export (2667 × 1500) | Regenerate with the build script |
| [final_architecture.pptx](final_architecture.pptx) | The same diagram as native, editable PowerPoint shapes | PowerPoint |
| [final_architecture.drawio](final_architecture.drawio) | The same nodes and edges for diagrams.net | <https://app.diagrams.net> or the VS Code draw.io extension |
| This file | Mermaid versions: logical architecture, workflow graph, ToT subgraph, review sequence | Any Mermaid renderer (GitHub renders them inline) |

The PNG, PPTX and draw.io files are generated from one definition,
`docs/final_capstone/build/diagram.py`:

```bash
python docs/final_capstone/build/build_architecture.py   # needs python-pptx; the PNG export also needs Microsoft PowerPoint
```

## 1. Logical architecture

```mermaid
flowchart LR
    IN[/"Synthetic OPRA request + synthetic record<br/>(data/, 'Synthetic: yes')"/]

    subgraph WF["LangGraph StateGraph workflow (app/workflows/graph.py)"]
        direction TB
        ORCH["Orchestrator Agent<br/>plan · route · merge · retry · step limit · escalate"]
        RA["Record Analysis Agent"]
        PR["Policy Research Agent"]
        QA["Compliance & QA Agent"]
        DEC{"Evidence decision"}
        STD["Standard ReAct path<br/>(CLEAR)"]
        TR["Targeted retrieval<br/>(MISSING, once)"]
        subgraph TOT["Conditional ToT subgraph (AMBIGUOUS / CONFLICTING)"]
            direction LR
            G[generate] --> E["evaluate<br/>(QA critic)"] --> P[prune] --> S[select / escalate]
            P -- next depth --> G
        end
        FIN["Finalize recommendations"]
        STATE[("Shared typed WorkflowState")]
        ORCH --> RA --> PR --> QA --> DEC
        DEC -- CLEAR --> STD --> FIN
        DEC -- "AMBIGUOUS / CONFLICTING" --> TOT --> FIN
        DEC -- MISSING --> TR --> PR
    end

    subgraph MCP["MCP tool layer (official MCP SDK, stdio)"]
        T1[detect_pii]
        T2[search_policy]
        T3[propose_redactions]
        T4[validate_citations]
    end

    subgraph KB["Knowledge and engines"]
        RAG[("Policy RAG<br/>BM25 + ChromaDB<br/>5 nj.gov documents, 496 chunks")]
        PII["Presidio + spaCy"]
        RULES["Redaction rules"]
    end

    subgraph HUMAN["Human review (mandatory)"]
        HR["LangGraph interrupt() →<br/>Streamlit review session"]
        RE["Re-evaluation ≤ 2 per finding"]
        AUTH["Final authorization"]
        RED["Deterministic span redaction<br/>(derivative; original unchanged)"]
        AUD[("Audit report<br/>hash-chained events")]
        HR --> AUTH --> RED --> AUD
        HR -. request .-> RE
    end

    FUT["Future (not implemented):<br/>LLM planner/critic · audio/video ·<br/>access control · persistent checkpoints"]:::future

    IN --> ORCH
    FIN --> HR
    RE -. feedback .-> ORCH
    RA -.-> T1
    PR -.-> T2
    QA -.-> T3
    QA -.-> T4
    TOT -.-> T4
    TOT -. one targeted search .-> T2
    T1 -.-> PII
    T2 -.-> RAG
    T4 -.-> RAG
    T3 -.-> RULES

    classDef future fill:#fafafa,stroke:#8a939b,stroke-dasharray: 5 5,color:#5f6b73
```

## 2. Workflow graph (nodes exactly as registered in `graph.py`)

```mermaid
flowchart TD
    S([START]) --> O1[orchestrator_start]
    O1 --> RA[record_analysis]
    RA -- FAILED --> ER[error_recovery]
    RA -- OK --> PR[policy_research<br/>parallel per category]
    ER -- retry once --> RA
    ER -- exhausted --> FR
    PR --> EM[evidence_merge]
    EM --> QA[compliance_qa]
    QA --> ED{evidence_decision}
    ED -- CLEAR --> FR[finalize_recommendations]
    ED -- "AMBIGUOUS / CONFLICTING" --> TOT[[tot subgraph]]
    ED -- "MISSING (once)" --> TR[targeted_retrieval]
    ED -- step limit --> FR
    TR --> EM
    TOT --> FR
    FR --> HR[/human_review<br/>interrupt: waits for a person/]
    HR --> C[complete] --> E([END])
```

Route priority at `evidence_decision`: step limit, then record-analysis failure, then MISSING
(if the retry is unused), then AMBIGUOUS or CONFLICTING (ToT), then standard.

## 3. ToT subgraph (`app/workflows/tot.py`)

```mermaid
flowchart LR
    A([finding with AMBIGUOUS or<br/>CONFLICTING evidence]) --> G["generate<br/>depth 1: ≤ 3 candidates from retrieved provisions<br/>(always includes a HUMAN_REVIEW candidate)"]
    G --> EV["evaluate (Compliance & QA critic)<br/>relevance 30 · citation validity 30<br/>consistency 25 · explanation 15<br/>validate_citations over MCP"]
    EV --> PRN["prune<br/>reject: fabricated/altered citation,<br/>unsupported factual assumption, REDACT with no evidence<br/>keep ≤ 2 (beam)"]
    PRN -- "depth < 3 and open branches" --> G
    PRN -- "done" --> SEL{"select"}
    SEL -- "REDACT branch: score ≥ 70, all assumptions SUPPORTED,<br/>operative provision cited" --> REC[SUPPORTED_RECOMMENDATION<br/>pending human approval]
    SEL -- "otherwise, or tie between different actions" --> ESC[ESCALATED<br/>unresolved questions listed]
```

Depth 2 (VERIFICATION) checks factual assumptions against the record and may spend the run's single
targeted retrieval. Depth 3 (CONCLUSION) records remaining gaps as unresolved questions.

## 4. Human review and finalization

```mermaid
sequenceDiagram
    participant WF as LangGraph workflow
    participant RS as ReviewSession (append-only, hash-chained)
    participant UI as Streamlit reviewer
    participant OR as Orchestrator agents
    WF->>RS: paused at human_review (interrupt), snapshot of findings, evidence, ToT, tool calls
    UI->>RS: approve / reject / modify (confirm spans) / add missed redaction (confirm)
    UI->>RS: request re-evaluation (reason, factual answers, optional query)
    RS->>OR: Policy Research + QA (+ ToT if still ambiguous), at most 2 per finding
    OR-->>RS: new recommendation, comparison with the previous one
    UI->>RS: finalize(reviewer, authorize = true)
    RS->>RS: blockers: unresolved findings, unacknowledged escalations, chain, source hash, span validation
    RS-->>UI: FINALIZED (derivative in outputs/, published = false) or FINALIZATION_BLOCKED with reasons
```
