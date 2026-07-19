"""Product-level Streamlit behavior tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from document_intelligence.models import DocumentStatus, JobStatus

from .conftest import FakeClient, make_document, make_job

if TYPE_CHECKING:
    from streamlit.testing.v1 import AppTest


def _visible_text(app: AppTest) -> str:
    values: list[str] = []
    for collection in (
        app.markdown,
        app.caption,
        app.info,
        app.success,
        app.warning,
        app.error,
        app.header,
        app.subheader,
    ):
        values.extend(str(item.value) for item in collection)
    return "\n".join(values)


def _button(app: AppTest, label: str):  # type: ignore[no-untyped-def]
    return next(item for item in app.button if item.label == label)


def _navigate(app: AppTest, section: str) -> AppTest:
    app.query_params["section"] = section
    return app.run()


def test_first_launch_is_a_complete_sample_onboarding(
    app_test: AppTest, fake_client: FakeClient
) -> None:
    result = app_test.run()

    assert not result.exception
    assert "Ask better questions of complex documents" in _visible_text(result)
    assert "Create sample workspace" in [item.label for item in result.button]
    _button(result, "Create sample workspace").click()
    result = result.run()

    assert not result.exception
    assert fake_client.sample_calls == 1
    assert result.session_state["section"] == "Activity"


def test_ready_workspace_asks_with_visible_document_scope(
    app_test: AppTest, fake_client: FakeClient
) -> None:
    fake_client.documents = [make_document()]
    result = app_test.run()

    assert "Ask across your documents" in _visible_text(result)
    question = next(item for item in result.text_area if item.label == "Your question")
    question.set_value("Which region missed target by the most?")
    _button(result, "Ask documents").click()
    result = result.run()

    assert not result.exception
    assert fake_client.asks[0].document_ids == ["doc-1"]
    assert "South missed its target by $0.7M." in _visible_text(result)
    assert any("docintel-answer" in str(item.value) for item in result.markdown)
    assert "page 2" in str(result.expander[0].label)


def test_navigation_prioritizes_core_work_and_groups_supporting_views(
    app_test: AppTest, fake_client: FakeClient
) -> None:
    fake_client.documents = [make_document()]
    result = app_test.run()

    navigation = next(
        str(item.value) for item in result.markdown if 'class="docintel-nav"' in str(item.value)
    )
    assert ">Ask</a>" in navigation
    assert ">Documents</a>" in navigation
    assert 'class="docintel-more"' in navigation
    assert "<summary" in navigation and ">More</summary>" in navigation
    assert ">Evidence</a>" in navigation
    assert ">Preparation</a>" in navigation
    assert ">Privacy</a>" in navigation


def test_core_questions_explain_scope_and_evidence_in_place(
    app_test: AppTest, fake_client: FakeClient
) -> None:
    fake_client.documents = [make_document()]
    result = app_test.run()

    scope = next(item for item in result.multiselect if item.label == "Documents to use")
    assert scope.help == "Only the selected documents can be used for this answer."
    assert any("docintel-info" in str(item.value) for item in result.markdown)
    assert any(
        "Evidence is the exact source material" in str(item.value) for item in result.markdown
    )


def test_suggested_question_survives_rerun_and_submits(
    app_test: AppTest, fake_client: FakeClient
) -> None:
    fake_client.documents = [make_document()]
    result = app_test.run()

    suggestion = "Do the chart months reconcile to the reported Q2 total?"
    _button(result, suggestion).click()
    result = result.run()

    assert not result.exception
    assert result.text_area[0].value == suggestion
    _button(result, "Ask documents").click()
    result = result.run()

    assert not result.exception
    assert fake_client.asks[-1].question == suggestion


def test_documents_view_exposes_upload_and_human_status(
    app_test: AppTest, fake_client: FakeClient
) -> None:
    fake_client.documents = [
        make_document(status=DocumentStatus.READY_WITH_WARNINGS),
    ]
    result = _navigate(app_test.run(), "Documents")

    assert not result.exception
    assert result.file_uploader[0].accept_multiple_files is True
    assert result.metric[0].value == "Ready with notes"
    assert "Prepare again" in [item.label for item in result.button]


def test_activity_uses_plain_language_and_retry(app_test: AppTest, fake_client: FakeClient) -> None:
    fake_client.documents = [make_document(status=DocumentStatus.PROCESSING)]
    fake_client.jobs = [make_job(status=JobStatus.RUNNING)]
    result = _navigate(app_test.run(), "Activity")

    assert not result.exception
    assert "Understanding visuals" in _visible_text(result)
    assert "In progress" in _visible_text(result)


def test_navigation_can_leave_a_requested_activity_section(
    app_test: AppTest, fake_client: FakeClient
) -> None:
    result = app_test.run()
    _button(result, "Create sample workspace").click()
    result = result.run()

    assert result.session_state["section"] == "Activity"
    fake_client.documents = [make_document()]
    result = _navigate(result, "Ask")

    assert not result.exception
    assert result.session_state["section"] == "Ask"
    assert "Ask across your documents" in _visible_text(result)


def test_system_discloses_deterministic_limit(app_test: AppTest) -> None:
    result = _navigate(app_test.run(), "System")

    assert "does not send document content" in _visible_text(result)
    assert "not equivalent to general multimodal model reasoning" in _visible_text(result)
