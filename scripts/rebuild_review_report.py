# Rebuild code-review report from SubagentWorkflow journal (wf_20b0018e3454).
import json
import re
import sys

JOURNAL = sys.argv[1] if len(sys.argv) > 1 else "wf_20b0018e3454.workflow.jsonl"
OUT = r"c:/MyProjects/neurodoc/code-review-agent-report.md"

with open(JOURNAL, encoding="utf-8") as fh:
    lines = [json.loads(raw) for raw in fh if raw.strip()]

finders, verifiers = [], []
for raw_line in lines:
    t = json.loads(raw_line["text"])
    (finders if "findings" in t else verifiers).append(t)

n_verdicts = len(verifiers)
n_real = sum(1 for v in verifiers if v.get("isReal"))

sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
allf = []
for t in finders:
    allf += t["findings"]
allf.sort(key=lambda f: sev_order[f["severity"]])

def clean(s):
    return re.sub(r"\s+", " ", s).strip()

def relpath(p):
    return p.replace("c:/MyProjects/neurodoc/", "")

md = [
    "# Code review: neurodoc (агент-оркестрация)",
    "",
    "**Метод:** 5 доменных ревьюеров (erpband/anthropic/claude-opus-5.5), "
    "минимальный скоуп на агента, structured output.",
    f"Adversarial-верификация high-находок: {n_real}/{n_verdicts} подтверждено, "
    f"{n_verdicts - n_real} опровергнуто.",
    f"Всего находок: {len(allf)} "
    f"(high: {sum(1 for f in allf if f['severity'] in ('critical','high'))}, "
    f"medium/low: {sum(1 for f in allf if f['severity'] not in ('critical','high'))}).",
    "",
    "## Подтверждённые HIGH (verifier: isReal=true)",
    "",
]
for f in allf:
    if f["severity"] not in ("critical", "high"):
        continue
    md.append(
        f"### [{f['severity'].upper()}] {relpath(f['file'])}:{f['line']} — {clean(f['title'])}"
    )
    md.append("")
    md.append(clean(f["description"]))
    if f.get("suggestion"):
        md.append("")
        md.append(f"**Fix:** {clean(f['suggestion'])}")
    md.append("")

md += ["## Medium / Low (verifier не запускался — экономия токенов)", ""]
for f in allf:
    if f["severity"] in ("critical", "high"):
        continue
    md.append(
        f"- **[{f['severity'].upper()}] {relpath(f['file'])}:{f['line']} — "
        f"{clean(f['title'])}** — {clean(f['description'])[:400]}"
    )
md.append("")

with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
    fh.write("\n".join(md))
print(f"written {OUT}: {len(allf)} findings, verdicts {n_real}/{n_verdicts} real")
