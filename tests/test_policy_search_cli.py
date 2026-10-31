from __future__ import annotations

import json

from app.policy_search import main


def test_cli_keyword_search_prints_verbatim_evidence(capsys):
    assert main(["social security number redaction", "--no-semantic", "-k", "2"]) == 0
    out = capsys.readouterr().out
    assert "STATUS: SUFFICIENT" in out
    assert "Source:    https://www.nj.gov/grc/custodians/redacting/" in out
    assert "Excerpt (verbatim):" in out


def test_cli_reports_insufficient_evidence(capsys):
    assert main(["recipe for chocolate chip cookies", "--no-semantic"]) == 0
    assert "STATUS: INSUFFICIENT" in capsys.readouterr().out


def test_cli_json_output_is_structured(capsys):
    assert main(["juvenile information exemption", "--no-semantic", "--json"]) == 0
    [result] = json.loads(capsys.readouterr().out)
    assert result["status"] == "SUFFICIENT"
    assert result["passages"][0]["chunk"]["source_url"].startswith("https://")


def test_cli_rejects_invalid_k(capsys):
    assert main(["telephone number", "--no-semantic", "-k", "0"]) == 1
