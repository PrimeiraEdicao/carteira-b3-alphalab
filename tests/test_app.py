from pathlib import Path

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]


def test_app_starts_with_empty_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHALAB_DATA_DIR", str(tmp_path))
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=15).run()

    assert not app.exception
    assert any("Escolha. Otimize. Entenda." in block.value for block in app.markdown)
    assert any("Atualize o catálogo" in message.value for message in app.info)
