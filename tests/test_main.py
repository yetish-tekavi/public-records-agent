from __future__ import annotations

from app.main import main


def test_main_summarises_real_dataset(real_data_dir, capsys):
    assert main(["--data-dir", str(real_data_dir)]) == 0
    out = capsys.readouterr().out
    assert "Loaded 3 synthetic review case(s)" in out
    for request_id in ("OPRA-TEST-001", "OPRA-TEST-002", "OPRA-TEST-003"):
        assert request_id in out


def test_main_returns_error_code_for_bad_data_dir(tmp_path):
    assert main(["--data-dir", str(tmp_path)]) == 1
