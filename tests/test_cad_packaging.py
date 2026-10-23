import sys

import pytest

from lineaforge.cad import AutoCadMcpBackend, CadUnavailable


def test_frozen_backend_selects_sibling_console_helper(tmp_path, monkeypatch):
    gui = tmp_path / "LineaForge.exe"
    helper = tmp_path / "LineaForgeCadServer.exe"
    helper.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(gui))
    root = tmp_path / "drawings"
    backend = AutoCadMcpBackend(output_root=root)
    assert backend.command == str(helper.resolve())
    assert backend.command != str(gui)
    assert backend.args == ["--output-root", str(root.resolve())]


def test_frozen_backend_without_helper_reports_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "LineaForge.exe"))
    with pytest.raises(CadUnavailable, match="sibling LineaForgeCadServer.exe.*source Python"):
        AutoCadMcpBackend(output_root=tmp_path)


def test_explicit_command_and_arguments_survive_frozen_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "LineaForge.exe"))
    custom_args = ["-m", "lineaforge.cad_server", "--output-root", str(tmp_path)]
    backend = AutoCadMcpBackend(command=r"C:\CustomPython\python.exe", args=custom_args, output_root=tmp_path)
    assert backend.command == r"C:\CustomPython\python.exe"
    assert backend.args == custom_args
    assert backend.args is not custom_args


def test_source_backend_keeps_python_module_invocation(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    backend = AutoCadMcpBackend(output_root=tmp_path)
    assert backend.command == sys.executable
    assert backend.args == ["-m", "lineaforge.cad_server", "--output-root", str(tmp_path.resolve())]
