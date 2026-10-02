# Working modes

> АГЕНТ: ЧИТАЙ ЭТОТ ФАЙЛ ЦЕЛИКОМ ДО ПОСЛЕДНЕЙ СТРОКИ.

## Three independent decisions

`route` describes the requested operation: create, audit or repair. `rigor`
describes verification depth: standard by default, strict for an explicit request
or material risk. `stop_at` describes the last creation artifact. Neither a short
deliverable nor a full export determines rigor.

Select strict when consequences of factual error, exact quantitative comparison,
critical topology or an explicit publication requirement justify the added checks.
Explain the risk briefly. A decorative preference, a style reference or ordinary
teaching use alone does not require strict.

## Shared obligations

Both modes preserve source truth, required entities, typed directed relationships,
containment, exact wording, numeric values/units/scales and reading order. Both
inspect a visible wireframe before styling and inspect the actual rendered output
at its intended viewing size before calling it final. Blockers cannot be averaged
away by visual quality scores.

Source, semantic and composition checks are internal gates. A checked/locked
artifact does not mean the user approved it. Do not request fresh approval at
each gate: continue the authorized operation. Honor explicit approval checkpoints,
the selected stop_at and unresolved critical facts. Do not invent missing facts.

## Standard

Use one [job brief](../templates/job-brief.md) to record plan, source/provenance,
semantic model, questions, composition, style, renderer and QA. Keep it concise
and specific. Separate YAML files are optional unless a machine validator actually
consumes them. A structured graph or preserve check requires its real schema
inputs; a prose-only claim that it passed is insufficient.

Keep one semantic truth source: when a graph validator is required, serialize the
brief's stable entities and relationships into the structured spec and render from
that same model. Reference the structured file in the brief; do not maintain a
contradictory prose topology. In structured containment, relationship from is the
container, to is the child and type is containment; wireframe nodes[].parent agrees.
Do not add entity.parent as another semantic field.

A single agent normally executes the roles in sequence. Request an independent
final critic when a second reviewer is available and authorized. Otherwise finish
the self-review, label it honestly, and disclose that independent review was
unavailable. This does not by itself block an ordinary standard deliverable.

G1: check facts, model and questions. G2: inspect topology and reading order in
the visible wireframe. G3: check actual export dimensions, text, values, clipping
and critical connectors. G4: answer the questions from the image, trace the
essential path and inspect target-size readability and redundant encodings.
Do not turn this into imagined timed human testing.

Full create delivery:

```text
job-brief.md           # includes source, decisions and short QA
wireframe.svg | wireframe.png
source.svg | source.html | source.pptx | equivalent editable source
final.png | final.pdf | equivalent requested export
```

The editable source can be the final SVG. An image-generator raster alone does
not satisfy editable source: provide a composite with editable labels, structured
relations and linked/embedded raster layers. If the renderer is unavailable,
deliver the last real artifact and describe the missing stage instead of claiming
a finished export. Include alt text in the brief where appropriate; no mandatory
separate document for a simple standard visual.

## Strict

Use production contract (`rigor: strict`), content packet, semantic spec, visual
spec, render brief, QA contract/report and relevant style/edit contracts. These
structured files must pass their schemas. Graph and preserve validations run on
actual compatible artifacts. Keep source/provenance and alt/long description.

Keep the same visible wireframe, editable source and export obligations as
standard. A YAML graph is additional evidence, never a visible composition.
G2 requires independent structural review; the final G3/G4 review requires an
independent critic. G1 can be checked by the orchestrator. Critics get sources,
requirements and artifacts without the author's justification.

If no independent reviewer is available, continue rendering, machine checks and
self-review. Record the missing independent G2/final review and return a prepared
candidate; strict release PASS remains pending. Do not masquerade a second prompt
in the same author's reasoning as independent review. Preserve unresolved G2
limitations when proceeding; a known structural blocker must be repaired first.

Strict G4 adds detailed path tracing, teach-back, applicable accessibility checks
and regression evidence. Human participant/timing claims require actual recorded
tests. A model first-impression review remains model evidence in either mode.

## stop_at and evidence

| stop_at | Required real artifact | Remaining work |
| --- | --- | --- |
| semantic_spec | Semantic section in brief, or schema spec in strict, with G1 | Composition and render |
| wireframe | Visible SVG/PNG with G1/G2 evidence | Style, final render and release QA |
| render_brief | Wireframe plus actionable renderer instructions | Execution and final QA |
| draft | Actual draft image and editable source, draft QA limitations | Unresolved repairs or release review |
| final | Requested export, editable source, applicable G1–G4 QA | Optional delivery packaging |
| release_package | Complete mode-appropriate delivery files | None unless QA marks an unmet requirement |

Default creation stop_at is release_package when the user requests a completed
infographic. A partial-stage result must not imply later gates passed.

For audit, return findings in concise Markdown (standard) or schema QA report
(strict). Missing truth prevents a factual PASS but does not prevent useful visual
findings. For repair, use an explicit preserve/modify scope in the brief or an edit
contract, with before/after evidence. Never create a new visual for audit alone.

Record check statuses `pass`, `fail`, `not_run` or `not_applicable`, evidence path
or visible region, and reviewer kind (`self`, `independent`, `human`). Distinguish
intermediate gate PASS (`advance_to_next_stage`) from release PASS (G4 plus all
applicable prerequisites). A required unperformed check remains unperformed;
optional independent standard review can be disclosed without misrepresenting
the checks actually completed.

Renderer count thresholds (for example 8 critical edges) and suggested semantic
budgets are configurable heuristics. They neither certify renderer reliability
nor allow errors below the threshold. Inspect the actual result.
