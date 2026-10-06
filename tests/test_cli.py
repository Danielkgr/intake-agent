import json
import os
from pathlib import Path

import pytest

from fakes import ENQUIRY, ScriptedAPI, final_answer
from intake_agent import __version__
from intake_agent.cli import load_dotenv, main


def test_version_flag_prints_the_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_triage_prints_a_record_from_a_text_enquiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = ScriptedAPI(final_answer(urgency="routine", routing="book_consult")).client()
    monkeypatch.setattr("intake_agent.agent.anthropic.Anthropic", lambda **_: client)
    path = tmp_path / "enquiry.txt"
    path.write_text(ENQUIRY.text, encoding="utf-8")

    code = main(["triage", str(path), "--received-date", "2026-09-29", "--name", "Nadia Ferreira-Holt", "--no-audit"])

    record = json.loads(capsys.readouterr().out)
    assert code == 0
    assert record["routing"]["decision"] == "book_consult"
    assert record["received_date"] == "2026-09-29"
    assert "audit" not in record


def test_triage_without_credentials_explains_what_to_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_credentials(self: object, enquiry: object) -> None:
        raise TypeError("Could not resolve authentication method.")

    monkeypatch.setattr("intake_agent.cli.ClaudeTriageAgent.triage", no_credentials)
    monkeypatch.setattr("intake_agent.agent.anthropic.Anthropic", lambda **_: None)
    path = tmp_path / "enquiry.txt"
    path.write_text("Hello", encoding="utf-8")
    assert main(["triage", str(path)]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_dotenv_sets_missing_variables_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nINTAKE_TEST_A=from-file\nexport INTAKE_TEST_B='quoted'\nINTAKE_TEST_C=\n")
    monkeypatch.setenv("INTAKE_TEST_A", "already-set")
    monkeypatch.delenv("INTAKE_TEST_B", raising=False)
    monkeypatch.delenv("INTAKE_TEST_C", raising=False)
    load_dotenv(env_file)
    assert os.environ["INTAKE_TEST_A"] == "already-set"
    assert os.environ["INTAKE_TEST_B"] == "quoted"
    assert "INTAKE_TEST_C" not in os.environ


def test_triage_with_the_baseline_runs_offline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "enquiry.txt"
    path.write_text("I want to register a trade mark for my bakery.", encoding="utf-8")
    assert main(["triage", str(path), "--baseline", "--received-date", "2026-09-29"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["arm"] == "baseline"
    assert record["routing"]["decision"] == "decline_and_refer"
