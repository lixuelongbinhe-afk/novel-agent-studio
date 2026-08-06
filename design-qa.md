# Design QA — Apple Ink & Glass (Option 2)

Date: 2026-08-05
final result: passed

## Comparison target

- Source visual truth: `work/product-design-audit/apple-option-2-ink-glass.png`
- Final browser implementation: `work/product-design-audit/implementation-final-1488x1058.png`
- Full-view comparison input: `work/product-design-audit/comparison-final.png` (source left, implementation right)
- Focused comparison input: `work/product-design-audit/comparison-focus-final.png` (manuscript and Agent console; source left, implementation right)
- Responsive evidence: `work/product-design-audit/responsive-1024x768.png` and `work/product-design-audit/responsive-800x700.png`
- Viewport and density: source 1488 × 1058 px; implementation 1488 × 1058 CSS px at device scale factor 1; no density normalization required.
- State: dark theme, collapsed activity rail, drafting phase, chapter selected, review inspector open, Agent console expanded. The source shows active review suggestions and running jobs; the implementation uses the same layout in a real idle-data state and does not fabricate product records.

## Final fidelity review

- Fonts and typography: the manuscript uses the existing Chinese serif stack at 18 px/2.2 line height, with an 18 px title and compact Segoe UI Variable chrome. The focused comparison confirms matching reading rhythm, hierarchy, and warm-paper contrast.
- Spacing and layout rhythm: the final implementation preserves the source's activity rail, chapter navigation, warm manuscript, right inspector, and bottom Agent console. The inspector and console proportions remain usable at 1024 px, while the inspector becomes an intentional overlay below 820 px.
- Colors and tokens: deep charcoal glass surfaces, low-opacity borders, warm ivory paper, and teal focus/primary states map to the source direction. Shadows and blur are restrained and do not introduce gradients.
- Image quality and asset fidelity: the source's Agent network sphere is implemented as a project-local ImageGen PNG (`frontend/src/assets/agent-network-orb.png`), not CSS/div art. Visible controls use the existing Lucide icon family; no handcrafted SVG or emoji stand-ins were added.
- Copy and content: all application-specific project, chapter, usage, review, and task text comes from real application state. The idle Agent copy explains the console without inventing jobs.
- Accessibility and motion: semantic buttons and labels remain keyboard reachable, focus styles remain visible, `MotionConfig` and the CSS reduced-motion fallback respect user motion preferences, and the generated orb has an empty alt because it is decorative.

## Comparison history

### Pass 1 — blocked

- [P1] Duplicate Studio title/tool row changed the source composition and pushed the editor down.
- [P2] Phase navigation used flat segmented tabs instead of a glass capsule surface.
- [P2] Review inspector and Agent console appeared abruptly and the console was too shallow.
- Evidence: `work/product-design-audit/implementation-pass1-1488x1058.png`.
- Fixes: merged workflow controls into the phase strip; introduced glass tokens, rounded spatial surfaces, inspector slide animation, stage transitions, and a 188 px expandable Agent console.

### Pass 2 — blocked

- [P2] The chapter rail was too narrow and the manuscript typography was smaller than the reference.
- [P2] The source's Agent network sphere was missing.
- Evidence: `work/product-design-audit/comparison-pass2.png`.
- Fixes: widened the desktop activity/chapter rails, increased manuscript title/body scale and padding, generated a real Agent network-sphere PNG with ImageGen, and integrated it into the live idle console.

### Final pass — passed

- Post-fix evidence: `work/product-design-audit/comparison-final.png` and `work/product-design-audit/comparison-focus-final.png`.
- No actionable P0, P1, or P2 visual differences remain. Dynamic review suggestions and running job rows differ only because the final capture deliberately reflects the real idle database state.

## Interactions and runtime checks

- Tested drafting-phase selection and stage transition.
- Tested review inspector close/open animation.
- Tested Agent console collapse/expand animation and restored expanded state.
- Tested 1488 × 1058, 1024 × 768, and 800 × 700 layouts.
- Browser console errors in final pass: 0.
- Frontend typecheck: passed.
- Frontend tests: 67 passed after the final asset and accessibility refinements.
- Production desktop packaging: application smoke test, GUI lifecycle test, extracted portable smoke test, and extracted portable GUI lifecycle test all passed.

## Follow-up polish

- [P3] A live running-job capture would demonstrate progress animation more vividly, but adding fake task records would reduce product truthfulness.
