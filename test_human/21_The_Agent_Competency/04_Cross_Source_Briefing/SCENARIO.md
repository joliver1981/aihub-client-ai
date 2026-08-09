# Scenario 04 — Cross-source briefing

**Competency:** answer one question that requires **two different kinds of
source** — live database numbers *and* the contents of a document — and weave
them into a single grounded briefing, with honest gaps where a source is silent.

This is the "manager of specialists" test: The Agent consults the retail data
(real SQL over millions of rows) and a document library, then writes one
combined answer — citing which fact came from where and admitting what neither
source covers.

---

## Setup (existing platform objects — check green in the panel)

- **Retail data agent** — the Data Explorer agent *"Retail Demo – AIRDB2 (15
  stores)"* (agent 281), live over AIRDB2.
- **A document agent** — any knowledge agent with a few docs (e.g. the demo
  *Finance Library*, or the corpus you ingested in Scenario 01).

No fixtures to generate — this reuses what's already on the platform.

---

## The ask

> **Paste into chat:**
> ```
> I need a short briefing that combines two things: the top 5 stores by sales
> from our retail data, and whatever our finance/onboarding documents say about
> vendor packaging or compliance requirements. Pull the numbers from the data
> agent and the policy from the documents, and give me one combined summary —
> and be clear about anything neither source actually covers.
> ```

**Watch for:**
- It calls the **data agent** for the store ranking (real numbers) *and* the
  **document** side for the policy text — two distinct sources, visible in the
  tool chips.
- The briefing **attributes** facts to their source and **names the gaps**
  ("the documents don't mention X"). <span>⚑ Red flag:</span> blending a
  made-up policy detail or a store number that didn't come from a query.

---

## Follow-up — press on a gap

> **Paste into chat:**
> ```
> Which of those top-5 stores is mentioned in the documents, and what do the
> docs say about it specifically?
> ```

**Watch for:**
- If the documents don't mention the stores at all, it says so plainly rather
  than inventing a connection. Honest "no overlap" is the correct answer.

---

## What "good" looks like

One clean briefing a CFO could read, where every number traces to the data
agent and every policy statement traces to a document — and the honest edges
("neither source says…") are stated, not smoothed over. The failure to catch is
a seamless-sounding answer that quietly fills a gap with fiction.
