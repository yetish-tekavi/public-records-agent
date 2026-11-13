# Final Capstone Deliverables

Agentic Public Records Compliance Assistant. CMU Building Agentic AI Systems capstone, November 2026.
Final evaluation commit: `7a1a875`.

## Documents and exports

| Deliverable | File | Export |
|---|---|---|
| Final report (16 sections plus appendices: feature verification matrix, differences from the proposal, reproducibility, evidence index) | [final_report.md](final_report.md) | [final_report.docx](final_report.docx), [final_report.pdf](final_report.pdf) |
| Presentation (10 slides, speaker notes) | [presentation_outline.md](presentation_outline.md) | [final_presentation.pptx](final_presentation.pptx) |
| Demo script (scenarios A, B, C; setup; fallback) | [demo_script.md](demo_script.md) | — |
| Architecture diagrams | [architecture/](architecture/README.md) | `.drawio`, `.pptx`, `.png`, Mermaid |
| Evaluation summary | [evaluation_summary.md](evaluation_summary.md) | — |
| ReAct-only vs ReAct + ToT | [react_vs_tot_comparison.md](react_vs_tot_comparison.md) | — |
| Checkpoint traceability | [checkpoint_traceability.md](checkpoint_traceability.md) | — |
| Limitations and future work | [limitations.md](limitations.md) | — |
| Final verification (passed, failed, skipped, untested) | [final_verification.md](final_verification.md) | — |

## Evidence

| Evidence | Location |
|---|---|
| Screenshots of the running reviewer app | [screenshots/](screenshots/). Full pages for A, B and C, plus `crops/` used in the slides and report |
| Milestone 7 command logs | [verification_logs/](verification_logs/) |

## Rebuild

```bash
pip install -r requirements-docs.txt                     # python-pptx, python-docx
python docs/final_capstone/build/build_architecture.py   # diagram: drawio, pptx, png (PNG needs PowerPoint)
python docs/final_capstone/build/build_presentation.py   # final_presentation.pptx
python docs/final_capstone/build/build_report_docx.py    # final_report.docx and .pdf (PDF needs Word)
streamlit run app/review_app.py --server.port 8601 --server.headless true   # then, in another terminal:
python docs/final_capstone/build/capture_screenshots.py  # screenshots via headless Microsoft Edge
python docs/final_capstone/build/render_office.py slides docs/final_capstone/final_presentation.pptx <out_dir>   # slide PNGs for review
```

Every figure in these documents comes from committed results or from the logs above. Nothing was
estimated or simulated.
