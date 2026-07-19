"""Document Intelligence Streamlit work app."""

from __future__ import annotations

from collections import Counter
from html import escape
from typing import Protocol, cast
from uuid import uuid4

import streamlit as st

from document_intelligence.config import Settings, get_settings
from document_intelligence.models import (
    Answer,
    ContentElement,
    Conversation,
    Document,
    DocumentStatus,
    IngestionJob,
    JobStatus,
    QueryRequest,
    SystemStatus,
    UploadReceipt,
)
from document_intelligence.ui.client import ApiClientError, DocumentIntelligenceClient
from document_intelligence.ui.presentation import (
    STATIC_STYLES,
    document_status_label,
    job_stage_label,
    job_status_label,
    modality_label,
)

PRIMARY_SECTIONS = ("Ask", "Documents")
SECONDARY_SECTIONS = {
    "Explore": "Evidence",
    "Activity": "Preparation",
    "System": "Privacy",
}
ALL_SECTIONS = (*PRIMARY_SECTIONS, *SECONDARY_SECTIONS)


class UiClient(Protocol):
    def status(self) -> SystemStatus: ...
    def list_documents(
        self, *, query: str | None = None, status: str | None = None, sort: str = "recent"
    ) -> list[Document]: ...
    def upload_documents(
        self, files: list[tuple[str, bytes, str]], *, idempotency_key: str
    ) -> list[UploadReceipt]: ...
    def load_sample(self) -> UploadReceipt: ...
    def get_document(self, document_id: str) -> Document: ...
    def list_elements(self, document_id: str) -> list[ContentElement]: ...
    def reprocess_document(self, document_id: str) -> IngestionJob: ...
    def delete_document(self, document_id: str) -> IngestionJob: ...
    def list_jobs(self) -> list[IngestionJob]: ...
    def retry_job(self, job_id: str) -> IngestionJob: ...
    def list_conversations(self) -> list[Conversation]: ...
    def ask(self, request: QueryRequest) -> Answer: ...
    def fetch_asset(self, asset_url: str) -> bytes: ...


@st.cache_resource(show_spinner=False)
def _cached_client() -> DocumentIntelligenceClient:
    return DocumentIntelligenceClient.from_settings(get_settings())


def _settings() -> Settings:
    injected = st.session_state.get("_docintel_settings")
    return injected if isinstance(injected, Settings) else get_settings()


def _client() -> UiClient:
    injected = st.session_state.get("_docintel_client")
    return cast("UiClient", injected) if injected is not None else _cached_client()


def _initialize_state() -> None:
    st.session_state.setdefault("section", "Ask")
    st.session_state.setdefault("selected_document_id", None)
    st.session_state.setdefault("last_answer", None)
    st.session_state.setdefault("ask_question", "")
    st.session_state.setdefault("notice", None)
    st.session_state.setdefault("confirm_delete", False)
    requested_section = st.session_state.pop("_requested_section", None)
    if requested_section in ALL_SECTIONS:
        st.session_state["section"] = requested_section
    query_section = st.query_params.get("section")
    if query_section in ALL_SECTIONS:
        st.session_state["section"] = query_section
    requested_question = st.session_state.pop("_requested_question", None)
    if isinstance(requested_question, str):
        st.session_state["ask_question"] = requested_question


def _request_section(section: str) -> None:
    st.session_state["section"] = section
    st.session_state["_requested_section"] = section
    st.query_params["section"] = section
    st.rerun()


def _render_navigation(active_section: str) -> None:
    links: list[str] = []
    for section in PRIMARY_SECTIONS:
        active = section == active_section
        attributes = ' class="active" aria-current="page"' if active else ""
        links.append(f'<a href="?section={section}" target="_self"{attributes}>{section}</a>')
    secondary_links: list[str] = []
    for section, label in SECONDARY_SECTIONS.items():
        active = section == active_section
        attributes = ' class="active" aria-current="page"' if active else ""
        secondary_links.append(
            f'<a href="?section={section}" target="_self"{attributes}>{label}</a>'
        )
    more_class = " active" if active_section in SECONDARY_SECTIONS else ""
    links.append(
        f'<details class="docintel-more"><summary class="{more_class.strip()}">More</summary>'
        '<div class="docintel-more-menu" aria-label="Supporting views">'
        + "".join(secondary_links)
        + "</div></details>"
    )
    st.markdown(
        '<nav class="docintel-nav" aria-label="Primary navigation">' + "".join(links) + "</nav>",
        unsafe_allow_html=True,
    )


def _hero(title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="docintel-hero"><div class="docintel-kicker">Document Intelligence</div>'
        f"<h1>{title}</h1><p>{subtitle}</p></div>",
        unsafe_allow_html=True,
    )


def _info_label(label: str, help_text: str) -> None:
    st.markdown(
        '<div class="docintel-inline-label"><strong>'
        f"{escape(label)}</strong>"
        '<span class="docintel-info" tabindex="0" aria-label="'
        f'{escape(help_text)}">i<span class="docintel-tooltip" role="tooltip">'
        f"{escape(help_text)}</span></span></div>",
        unsafe_allow_html=True,
    )


def _safe_status(client: UiClient) -> tuple[SystemStatus | None, ApiClientError | None]:
    try:
        return client.status(), None
    except ApiClientError as exc:
        return None, exc


def _safe_documents(
    client: UiClient,
    *,
    query: str | None = None,
    status: str | None = None,
    sort: str = "recent",
) -> list[Document]:
    try:
        return client.list_documents(query=query, status=status, sort=sort)
    except ApiClientError as exc:
        st.error(str(exc))
        return []


def _render_connection_error(error: ApiClientError) -> None:
    _hero(
        "Your document workspace is almost ready",
        "The interface is running, but the document service has not answered yet.",
    )
    st.error(str(error))
    st.markdown(
        '<div class="docintel-card"><strong>Start the workspace</strong><br>'
        "Run <code>make demo</code> from the project folder, then refresh this page.</div>",
        unsafe_allow_html=True,
    )
    if st.button("Try again", type="primary"):
        st.rerun()


def _render_onboarding(client: UiClient, status: SystemStatus) -> None:
    _hero(
        "Ask better questions of complex documents",
        "Bring together text, tables, charts, diagrams, and scanned pages—then inspect "
        "the exact evidence behind every answer.",
    )
    left, middle, right = st.columns(3)
    with left:
        st.markdown(
            '<div class="docintel-stat"><span>1 · Add</span><strong>Documents</strong></div>',
            unsafe_allow_html=True,
        )
    with middle:
        st.markdown(
            '<div class="docintel-stat"><span>2 · Prepare</span><strong>Evidence</strong></div>',
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            '<div class="docintel-stat"><span>3 · Ask</span><strong>With sources</strong></div>',
            unsafe_allow_html=True,
        )
    st.write("")
    primary, secondary = st.columns([1, 1.15])
    with primary:
        st.subheader("Try the sample review")
        st.write(
            "An eight-page operations review exercises tables, charts, a scanned memo, "
            "and a process diagram. No provider key is needed."
        )
        if st.button("Create sample workspace", type="primary", use_container_width=True):
            with st.spinner("Preparing the Northstar review…"):
                try:
                    client.load_sample()
                    st.session_state["notice"] = (
                        "The sample was accepted. Its evidence is being prepared."
                    )
                    _request_section("Activity")
                except ApiClientError as exc:
                    st.error(str(exc))
    with secondary:
        st.subheader("Add your own PDFs")
        st.write(
            "Use Documents for up to 10 PDFs at once. Files stay on this machine in "
            "deterministic mode."
        )
        if st.button("Open Documents", use_container_width=True):
            _request_section("Documents")
    if status.provider_mode != "deterministic":
        st.info(
            "External model mode is enabled. Relevant document excerpts and images may "
            "be sent to the configured provider."
        )


def _answer_card(client: UiClient, answer: Answer) -> None:
    st.subheader("Answer")
    if answer.abstained:
        st.warning(answer.text)
    else:
        st.markdown(
            f'<div class="docintel-answer">{escape(answer.text)}</div>',
            unsafe_allow_html=True,
        )
    if answer.modalities_used:
        st.caption(
            "Evidence used: " + " · ".join(modality_label(item) for item in answer.modalities_used)
        )
    st.divider()
    _info_label(
        f"Evidence ({len(answer.citations)})",
        "Each item is stored source material used for this answer. Open it to inspect the "
        "quoted passage or page image.",
    )
    for index, citation in enumerate(answer.citations, start=1):
        with st.expander(
            f"{index}. {citation.document_name} · page {citation.page_number} · "
            f"{modality_label(citation.modality)}",
            expanded=index == 1,
        ):
            st.write(citation.excerpt)
            if citation.asset_url and citation.available:
                try:
                    st.image(client.fetch_asset(citation.asset_url), use_container_width=True)
                except ApiClientError:
                    st.caption(
                        "The page preview is temporarily unavailable; the citation text "
                        "remains preserved."
                    )
            if not citation.available:
                st.caption("The original source was removed after this answer was saved.")


def _render_ask(client: UiClient, documents: list[Document]) -> None:
    _hero(
        "Ask across your documents",
        "Select the PDFs to use, ask in plain language, then inspect the supporting sources.",
    )
    ready = [
        item
        for item in documents
        if item.status in {DocumentStatus.READY, DocumentStatus.READY_WITH_WARNINGS}
    ]
    if not ready:
        st.info(
            "No documents are ready yet. Add a PDF or check Activity while the sample is prepared."
        )
        left, right = st.columns(2)
        if left.button("Add documents", type="primary", use_container_width=True):
            _request_section("Documents")
        if right.button("View activity", use_container_width=True):
            _request_section("Activity")
        return

    labels = {item.id: item.display_name for item in ready}
    _info_label(
        "Question and document scope",
        "Evidence is the exact source material behind an answer: text, table rows, charts, "
        "diagrams, images, or scanned text. Only selected documents are searched.",
    )
    with st.form("ask-form"):
        selected = st.multiselect(
            "Documents to use",
            options=list(labels),
            default=list(labels),
            format_func=lambda value: labels[value],
            help="Only the selected documents can be used for this answer.",
        )
        question = st.text_area(
            "Your question",
            key="ask_question",
            placeholder="Compare the regional table with the monthly revenue chart…",
            height=105,
            max_chars=4000,
        )
        submitted = st.form_submit_button(
            "Ask documents", type="primary", use_container_width=True, disabled=not selected
        )
    if submitted:
        if not question.strip():
            st.warning("Enter a question before asking your documents.")
        else:
            with st.spinner("Finding and checking evidence…"):
                try:
                    fresh_answer = client.ask(
                        QueryRequest(question=question, document_ids=selected, top_k=10)
                    )
                    st.session_state["last_answer"] = fresh_answer
                except ApiClientError as exc:
                    st.error(str(exc))

    saved_answer = st.session_state.get("last_answer")
    if isinstance(saved_answer, Answer):
        _answer_card(client, saved_answer)
    else:
        st.subheader("Good questions to start with")
        suggestions = (
            "Which region missed its target by the largest amount?",
            "Do the chart months reconcile to the reported Q2 total?",
            "What caused the June 14 packing interruption, how long did it last, and how many "
            "orders were delayed?",
            "Summarize the operating risk using text, tables, charts, and scanned evidence.",
        )
        for suggestion in suggestions:
            if st.button(suggestion, key=f"suggest-{suggestion}", use_container_width=True):
                st.session_state["_requested_question"] = suggestion
                st.rerun()


def _render_documents(client: UiClient) -> None:
    _hero(
        "Documents",
        "Add and manage the PDFs available to your questions. Preparation starts automatically.",
    )
    with st.expander("Add PDFs", expanded=True):
        uploads = st.file_uploader(
            "Choose up to 10 PDF files",
            type=["pdf"],
            accept_multiple_files=True,
            help=(
                "Each PDF may be up to 50 MB and 250 pages. File content is validated "
                "by the server."
            ),
        )
        if uploads:
            total_bytes = sum(upload.size for upload in uploads)
            st.caption(f"{len(uploads)} selected · {total_bytes / (1024 * 1024):.1f} MB total")
        if st.button(
            "Add to workspace",
            type="primary",
            disabled=not uploads or len(uploads) > 10,
        ):
            files = [
                (item.name, item.getvalue(), item.type or "application/pdf") for item in uploads
            ]
            with st.spinner("Accepting documents…"):
                try:
                    receipts = client.upload_documents(files, idempotency_key=str(uuid4()))
                    st.success(
                        f"{len(receipts)} document{'s' if len(receipts) != 1 else ''} accepted."
                    )
                except ApiClientError as exc:
                    st.error(str(exc))

    filter_col, status_col, sort_col = st.columns([1.6, 1, 1])
    query = filter_col.text_input("Find a document", placeholder="Search by name")
    status = status_col.selectbox(
        "Status",
        options=["All", *[item.value for item in DocumentStatus if item != DocumentStatus.DELETED]],
        format_func=lambda value: value.replace("_", " ").title(),
    )
    sort = sort_col.selectbox(
        "Sort",
        options=["recent", "name_asc", "name_desc"],
        format_func=lambda value: {
            "recent": "Recently added",
            "name_asc": "Name A-Z",
            "name_desc": "Name Z-A",
        }[value],
    )
    documents = _safe_documents(client, query=query or None, status=status, sort=sort)
    if not documents:
        st.info("No documents match these filters.")
        return

    labels = {
        item.id: f"{item.display_name} · {document_status_label(item.status)}" for item in documents
    }
    if st.session_state.get("selected_document_id") not in labels:
        st.session_state["selected_document_id"] = next(iter(labels))
    selected_id = st.selectbox(
        "Document",
        options=list(labels),
        format_func=lambda value: labels[value],
        key="selected_document_id",
    )
    selected = next(item for item in documents if item.id == selected_id)
    st.markdown('<div class="docintel-card">', unsafe_allow_html=True)
    st.subheader(selected.display_name)
    metrics = st.columns(4)
    metrics[0].metric("Status", document_status_label(selected.status))
    metrics[1].metric("Pages", selected.page_count or "—")
    metrics[2].metric("Evidence items", selected.element_count)
    metrics[3].metric("Notes", selected.warning_count)
    st.markdown("</div>", unsafe_allow_html=True)
    actions = st.columns([1, 1, 2])
    if actions[0].button("Prepare again", disabled=selected.status == DocumentStatus.PROCESSING):
        try:
            client.reprocess_document(selected.id)
            st.success("Reprocessing was queued.")
        except ApiClientError as exc:
            st.error(str(exc))
    if actions[1].button("Remove…"):
        st.session_state["confirm_delete"] = True
    if st.session_state.get("confirm_delete"):
        st.warning(
            "Removing a document deletes its stored PDF, page images, extracted evidence, "
            "and active vectors. Saved answers retain citation tombstones."
        )
        confirm, cancel = st.columns(2)
        if confirm.button("Confirm removal", type="primary"):
            try:
                client.delete_document(selected.id)
                st.session_state["confirm_delete"] = False
                st.success("Removal was queued and will be verified before completion.")
                st.rerun()
            except ApiClientError as exc:
                st.error(str(exc))
        if cancel.button("Cancel"):
            st.session_state["confirm_delete"] = False
            st.rerun()


def _render_explore(client: UiClient, documents: list[Document]) -> None:
    _hero(
        "Explore extracted evidence",
        "Inspect the text, tables, visuals, and scans found in each prepared PDF.",
    )
    ready = [
        item
        for item in documents
        if item.status in {DocumentStatus.READY, DocumentStatus.READY_WITH_WARNINGS}
    ]
    if not ready:
        st.info("Evidence appears here after a document is ready.")
        return
    labels = {item.id: item.display_name for item in ready}
    selected_id = st.selectbox(
        "Document to explore", list(labels), format_func=lambda value: labels[value]
    )
    try:
        elements = client.list_elements(selected_id)
    except ApiClientError as exc:
        st.error(str(exc))
        return
    counts = Counter(element.modality for element in elements)
    columns = st.columns(min(4, max(1, len(counts))))
    for column, (modality, count) in zip(columns, counts.items(), strict=False):
        column.metric(modality_label(modality), count)
    modalities = sorted(counts, key=lambda item: modality_label(item))
    selected_modalities = st.multiselect(
        "Evidence types",
        modalities,
        default=modalities,
        format_func=modality_label,
        help="Filter the extracted source material shown below; this does not change the PDF.",
    )
    filtered = [item for item in elements if item.modality in selected_modalities]
    for element in filtered[:100]:
        with st.expander(
            f"Page {element.page_number} · {modality_label(element.modality)}",
            expanded=False,
        ):
            st.write(element.content)
            if element.confidence < 0.8:
                st.caption(f"Lower-confidence extraction · {element.confidence:.0%}")


def _render_activity(client: UiClient) -> None:
    _hero(
        "Preparation",
        "See when a PDF is ready for questions and retry work that needs attention.",
    )
    if st.session_state.get("notice"):
        st.success(st.session_state.pop("notice"))
    refresh, _ = st.columns([1, 4])
    if refresh.button("Refresh", use_container_width=True):
        st.rerun()
    try:
        jobs = client.list_jobs()
    except ApiClientError as exc:
        st.error(str(exc))
        return
    if not jobs:
        st.info("No document activity yet.")
        return
    for job in jobs:
        st.markdown('<div class="docintel-card">', unsafe_allow_html=True)
        heading, status_column = st.columns([2.5, 1])
        heading.write(f"**{job.kind.value.replace('_', ' ').title()}**")
        status_column.caption(job_status_label(job.status))
        st.caption(job_stage_label(job.stage))
        st.progress(job.progress, text=job_stage_label(job.stage))
        if job.error_message:
            st.error(job.error_message)
        if job.status == JobStatus.FAILED and st.button("Retry", key=f"retry-{job.id}"):
            try:
                client.retry_job(job.id)
                st.success("Retry queued.")
                st.rerun()
            except ApiClientError as exc:
                st.error(str(exc))
        st.markdown("</div>", unsafe_allow_html=True)


def _render_system(status: SystemStatus, settings: Settings) -> None:
    _hero(
        "Privacy and system",
        "Check whether document content stays local or may be sent to a configured provider.",
    )
    provider_label = (
        "Credential-free local sample" if status.provider_mode == "deterministic" else "OpenAI"
    )
    _info_label(
        "Active processing mode",
        "Deterministic mode keeps the sample workflow local. OpenAI mode may send only the "
        "retrieved excerpts and selected images needed to answer.",
    )
    st.metric("Answer and visual provider", provider_label)
    st.metric("Embedding provider", status.embedding_provider.title())
    st.metric("OCR", "Available" if status.ocr_available else "Optional component not installed")
    if status.provider_mode == "deterministic":
        st.success("Current mode does not send document content to an external model provider.")
        st.caption(
            "Deterministic answers are designed for the synthetic sample and evidence "
            "inspection; they are not equivalent to general multimodal model reasoning."
        )
    else:
        st.warning(
            "Relevant excerpts and selected page or crop images may be sent to OpenAI for "
            "visual understanding and answer generation. Provider credentials remain server-side."
        )
    with st.expander("Limits and runtime details"):
        st.write(f"Maximum PDF size: {settings.max_file_bytes // (1024 * 1024)} MB")
        st.write(f"Maximum pages per PDF: {settings.max_pages}")
        st.write(f"Maximum files per upload: {settings.max_upload_batch}")
        st.write(f"API: {settings.resolved_api_base_url}")
        st.write("Public exposure requires TLS and an identity-aware access layer.")
    if status.warnings:
        st.subheader("Needs attention")
        for warning in status.warnings:
            st.warning(warning)


def main() -> None:
    st.set_page_config(
        page_title="Document Intelligence",
        page_icon="📄",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.markdown(STATIC_STYLES, unsafe_allow_html=True)
    _initialize_state()
    settings = _settings()
    client = _client()
    status, error = _safe_status(client)
    if error or status is None:
        _render_connection_error(error or ApiClientError("The workspace is unavailable."))
        return

    active_section = st.session_state["section"]
    _render_navigation(active_section)

    documents = _safe_documents(client)
    if not documents and st.session_state["section"] == "Ask":
        _render_onboarding(client, status)
        return
    section = st.session_state["section"]
    if section == "Ask":
        _render_ask(client, documents)
    elif section == "Documents":
        _render_documents(client)
    elif section == "Explore":
        _render_explore(client, documents)
    elif section == "Activity":
        _render_activity(client)
    else:
        _render_system(status, settings)


if __name__ == "__main__":
    main()
