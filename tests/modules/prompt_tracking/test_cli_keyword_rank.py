"""CLI wiring for the `--no-keyword-rank` switch and the keyword-call line in output."""

from __future__ import annotations

from tests.modules.prompt_tracking.test_cli import (
    REQUIRED,
    _summary,
    build_parser,
    fake_pipeline,  # noqa: F401 - fixture
    main,
)


def test_flag_parses_off_by_default():
    assert build_parser().parse_args(REQUIRED).no_keyword_rank is False
    assert build_parser().parse_args([*REQUIRED, "--no-keyword-rank"]).no_keyword_rank is True


def test_flag_disables_keyword_rank_in_payload(fake_pipeline):  # noqa: F811
    assert main([*REQUIRED, "--approve-spend"]) == 0
    assert fake_pipeline.instances[-1].payload.track_keyword_rank is True
    assert main([*REQUIRED, "--approve-spend", "--no-keyword-rank"]) == 0
    assert fake_pipeline.instances[-1].payload.track_keyword_rank is False


def test_text_output_reports_keyword_rank_calls(fake_pipeline, capsys):  # noqa: F811
    fake_pipeline.result = fake_pipeline.result.model_copy(
        update={"data": _summary(keyword_rank_calls=3)}
    )
    assert main([*REQUIRED, "--approve-spend"]) == 0
    assert "keyword rank calls: 3" in capsys.readouterr().out
