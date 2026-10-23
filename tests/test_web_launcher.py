"""A repeat launch must never reopen/reset the active durable job store."""
import socket
from lineaforge import web_launcher


def test_busy_port_does_not_open_job_store(tmp_path,monkeypatch):
    frontend=tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<!doctype html>")
    monkeypatch.setattr(web_launcher,"existing_instance",lambda *args:False)
    def forbidden(*args,**kwargs):
        raise AssertionError("A busy port must not construct another JobManager")
    monkeypatch.setattr("lineaforge.web_api.create_app",forbidden)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1",0));listener.listen()
        assert web_launcher.main(["--port",str(listener.getsockname()[1]),"--frontend-dir",str(frontend),"--data-dir",str(tmp_path / "data")])==2
    assert not (tmp_path / "data").exists()
