import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("update_content", ROOT / "scripts" / "update_content.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_stable_id():
    assert mod.stable_id("https://example.com/a", "x") == mod.stable_id("https://example.com/a", "y")


def test_classify():
    assert mod.classify("TSMC expands advanced packaging", "", "Other") == "Semiconductor"
    assert mod.classify("Military drill near Taiwan Strait", "", "Other") == "Security"


def test_clean_text():
    assert mod.clean_text("<b>A</b> &amp; B") == "A & B"
