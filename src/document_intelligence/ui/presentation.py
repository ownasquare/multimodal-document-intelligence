"""Shared visual language and human-readable presentation helpers."""

from __future__ import annotations

from document_intelligence.models import DocumentStatus, JobStage, JobStatus, Modality

STATIC_STYLES = """
<style>
  :root {
    --canvas: #f6f7f4;
    --surface: #ffffff;
    --surface-soft: #eef3f0;
    --ink: #17211e;
    --muted: #61706a;
    --line: #dce4df;
    --accent: #0f766e;
    --accent-deep: #0a5b55;
    --danger: #b42318;
    --warning: #9a6700;
    --radius: 12px;
  }
  .stApp { background: var(--canvas); color: var(--ink); }
  .block-container { max-width: 1180px; padding-top: 1.4rem; padding-bottom: 4rem; }
  [data-testid="stHeader"] { background: transparent; height: 0; min-height: 0; }
  [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu { display: none !important; }
  [data-testid="stSidebar"] { background: #f0f2ee; border-right: 1px solid var(--line); }
  h1, h2, h3 { letter-spacing: -0.025em; color: var(--ink); }
  h1 { font-size: clamp(1.75rem, 4vw, 2.55rem) !important; }
  p, label, [data-testid="stCaptionContainer"] { color: var(--muted); }
  .docintel-kicker { color: var(--accent); font-size: .75rem; font-weight: 750; letter-spacing: .1em; text-transform: uppercase; }
  .docintel-nav { display: flex; align-items: center; gap: .6rem; margin: 0 0 1rem; position: relative; z-index: 20; }
  .docintel-nav > a, .docintel-more > summary { display: inline-flex; justify-content: center; align-items: center; min-width: 7rem; min-height: 2.55rem; padding: .5rem .95rem; border: 1px solid #bdc9c3; border-radius: 9px; color: #44534d !important; font-weight: 650; text-decoration: none !important; background: transparent; cursor: pointer; }
  .docintel-nav > a:hover, .docintel-more > summary:hover { border-color: var(--accent); color: var(--accent-deep) !important; background: var(--surface-soft); }
  .docintel-nav > a.active, .docintel-more > summary.active { color: #fff !important; background: var(--accent); border-color: var(--accent); }
  .docintel-more { position: relative; margin-left: auto; }
  .docintel-more > summary { list-style: none; }
  .docintel-more > summary::-webkit-details-marker { display: none; }
  .docintel-more > summary::after { content: "⌄"; margin-left: .45rem; font-size: .9rem; }
  .docintel-more[open] > summary::after { content: "⌃"; }
  .docintel-more-menu { position: absolute; right: 0; top: calc(100% + .4rem); display: grid; gap: .2rem; min-width: 12rem; padding: .4rem; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); box-shadow: 0 14px 32px rgba(23, 33, 30, .13); }
  .docintel-more-menu a { padding: .58rem .7rem; border-radius: 7px; color: #44534d !important; font-weight: 620; text-decoration: none !important; }
  .docintel-more-menu a:hover { color: var(--accent-deep) !important; background: var(--surface-soft); }
  .docintel-more-menu a.active { color: #fff !important; background: var(--accent); }
  .docintel-hero { border-bottom: 1px solid var(--line); padding: .25rem 0 1.15rem; margin-bottom: 1rem; }
  .docintel-hero p { max-width: 720px; margin: .25rem 0 0; }
  .docintel-card { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 1rem 1.05rem; margin: .35rem 0 .75rem; }
  .docintel-card strong { color: var(--ink); }
  .docintel-answer { color: var(--ink); line-height: 1.65; white-space: pre-wrap; }
  .docintel-stat { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: .8rem .9rem; min-height: 86px; }
  .docintel-stat span { display: block; color: var(--muted); font-size: .78rem; }
  .docintel-stat strong { display: block; color: var(--ink); font-size: 1.4rem; margin-top: .15rem; }
  .docintel-badge { display: inline-flex; align-items: center; border-radius: 999px; padding: .17rem .55rem; background: var(--surface-soft); color: var(--accent-deep); font-size: .76rem; font-weight: 650; }
  .docintel-evidence { border-left: 3px solid var(--accent); background: #fbfcfa; border-radius: 0 9px 9px 0; padding: .72rem .9rem; margin: .55rem 0; }
  .docintel-evidence p { margin: .2rem 0; }
  .docintel-inline-label { display: flex; align-items: center; gap: .45rem; margin: .35rem 0 .45rem; color: var(--ink); }
  .docintel-info { position: relative; display: inline-flex; align-items: center; justify-content: center; width: 1.15rem; height: 1.15rem; border: 1px solid #9baba4; border-radius: 999px; color: var(--muted); font-size: .72rem; font-weight: 750; cursor: help; }
  .docintel-tooltip { visibility: hidden; opacity: 0; position: absolute; z-index: 50; left: 50%; bottom: calc(100% + .5rem); width: min(19rem, 76vw); padding: .58rem .68rem; border: 1px solid var(--line); border-radius: 8px; background: #17211e; color: #fff; font-size: .78rem; font-weight: 500; line-height: 1.35; transform: translateX(-50%); box-shadow: 0 8px 24px rgba(23, 33, 30, .18); pointer-events: none; }
  .docintel-info:hover .docintel-tooltip, .docintel-info:focus .docintel-tooltip { visibility: visible; opacity: 1; }
  .stButton > button, .stDownloadButton > button { border-radius: 9px; min-height: 2.55rem; font-weight: 650; }
  .stButton > button[kind="primary"] { background: var(--accent); border-color: var(--accent); color: white; }
  .stButton > button[kind="primary"]:hover { background: var(--accent-deep); border-color: var(--accent-deep); }
  .stTextInput input, .stTextArea textarea, [data-baseweb="select"] > div { border-radius: 9px !important; }
  [data-testid="stFileUploader"] { background: var(--surface); border: 1px dashed #aebdb6; border-radius: var(--radius); padding: .55rem; }
  :focus-visible { outline: 3px solid rgba(15, 118, 110, .32) !important; outline-offset: 2px; }
  @media (max-width: 700px) {
    .block-container { padding: .9rem .8rem 3rem; }
    .docintel-hero p { font-size: .93rem; }
    .docintel-nav { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .4rem; }
    .docintel-nav > a, .docintel-more > summary { min-width: 0; width: 100%; padding-inline: .45rem; }
    .docintel-more { margin-left: 0; min-width: 0; }
    .docintel-more-menu { right: 0; min-width: min(12rem, 82vw); }
    [data-testid="stHorizontalBlock"] { gap: .45rem; }
  }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; animation: none !important; }
  }
</style>
"""


def document_status_label(status: DocumentStatus) -> str:
    return {
        DocumentStatus.QUEUED: "Waiting to be read",
        DocumentStatus.PROCESSING: "Preparing evidence",
        DocumentStatus.READY: "Ready",
        DocumentStatus.READY_WITH_WARNINGS: "Ready with notes",
        DocumentStatus.FAILED: "Needs attention",
        DocumentStatus.DELETING: "Removing",
        DocumentStatus.DELETED: "Removed",
    }[status]


def job_status_label(status: JobStatus) -> str:
    return {
        JobStatus.QUEUED: "Waiting",
        JobStatus.RUNNING: "In progress",
        JobStatus.SUCCEEDED: "Completed",
        JobStatus.FAILED: "Needs attention",
        JobStatus.CANCELLED: "Cancelled",
    }[status]


def job_stage_label(stage: JobStage) -> str:
    return {
        JobStage.QUEUED: "Waiting to begin",
        JobStage.READING: "Reading document",
        JobStage.EXTRACTING_TEXT: "Finding text",
        JobStage.EXTRACTING_TABLES: "Reconstructing tables",
        JobStage.OCR: "Reading scanned regions",
        JobStage.UNDERSTANDING_VISUALS: "Understanding visuals",
        JobStage.INDEXING: "Preparing search",
        JobStage.VERIFYING: "Checking evidence",
        JobStage.DELETING: "Removing document data",
        JobStage.COMPLETE: "Complete",
    }[stage]


def modality_label(modality: Modality) -> str:
    return {
        Modality.TEXT: "Text",
        Modality.TABLE: "Table",
        Modality.TABLE_ROW: "Table row",
        Modality.IMAGE: "Image",
        Modality.CHART: "Chart",
        Modality.DIAGRAM: "Diagram",
        Modality.OCR: "Scanned text",
        Modality.PAGE_SUMMARY: "Page overview",
    }[modality]


def format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"
