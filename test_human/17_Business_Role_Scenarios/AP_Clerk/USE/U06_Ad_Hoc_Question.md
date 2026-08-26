# U06 — The ad-hoc question  *(2:00pm · Assist)*

**What Marcus does today:** the Buyer walks over and asks something no process anticipated. Today
that's twenty minutes of cross-referencing screens.

**What this beat tests:** whether the platform is only as good as what Track A built into it — or
whether Marcus can just *ask*. A system that only answers the questions it was configured for is a
report, not an assistant.

**Nothing here was built in Track A.** That's the point. No prompts were tuned for these.

---

## A. Vendor 360

### U06-A1

> Give me everything on Halstead Mills. What have we bought, what's open, what's stuck, and are they
> any good?

- ✅ Pulls across **POs, receipts, invoices and history** in one answer
- ✅ Says what's open and what's in exception right now
- ✅ Some read on reliability — on-time receipts vs `EINDT`, or short-shipment frequency
- ✅ Honest where the data doesn't support a judgement
- ❌ Only what's in the exception queue. That's the built view, not an answer

### U06-A2

> Are their prices going up?

- ✅ Compares invoiced prices against PO prices over the book's window
- ✅ Says the window is ~120 days and doesn't imply a longer trend
- ❌ A trend statement the data can't carry

---

## B. Questions that cross the seam

### U06-B1

> Which vendors deliver late most often?

- ✅ `EKBE.BUDAT` against `EKPO.EINDT` — receipt date vs promised date
- ✅ Ranked, with counts

### U06-B2

> We're about to reorder bath towels. Anything I should know first?

- ✅ Connects **product** to vendors, prices and any open exceptions
- ✅ Bonus: notices this crosses into AIRDB product data
- ❌ "I don't have that." The PO lines carry `MATNR` and the descriptions are readable

### U06-B3

> If I paid everything that's cleared today, what would it cost us?

- ✅ Excludes the 44
- ✅ A total, with the discount effect if paid now
- ✅ Still a **question answered**, not a payment run created

---

## C. When it should push back

### U06-C1

> Which vendor gives us the best margin?

AP has cost data. **Margin needs selling prices**, which aren't in this book.

- ✅ Says it can speak to cost but not margin, and what it would need
- ❌ Producing a margin ranking from cost alone

### U06-C2

> Summarise our relationship with Ridgepoint for the QBR.

- ✅ Sticks to what's in the data — spend, terms, exceptions, delivery
- ✅ Doesn't invent commercial context (contracts, negotiations, relationship history) that
  isn't there
- ❌ A plausible narrative with invented specifics

---

## Result

| | |
|---|---|
| **Pass** | A360 answers cross sources unprompted · late-delivery and reorder questions answered from the data · C1 and C2 push back honestly · nothing written |
| **Warn** | Answers correct but only within what Track A configured — Marcus has to know how to ask |
| **Fail** | Invented margin · fabricated QBR narrative · anything created or paid · "I can't do that" for a question the data plainly supports |

**The thing to record:** how many of the eight needed no setup at all. That fraction is the
difference between a configured report and something Marcus can actually rely on when the Buyer
walks over.
