from __future__ import annotations
import sys

from . import APP_NAME, __version__


def packaged_self_test() -> int:
    """Minimal frozen-binary health check used by release pipelines/installers."""
    try:
        from .core import legacy1710_engine
        from .core.jar_analyzer import analyze_jar  # noqa: F401
        from .core.modpack_analyzer import analyze_modpack  # noqa: F401
        from .core.version_targets import TARGET_BY_VERSION

        target = TARGET_BY_VERSION.get("1.7.10")
        if target is None or target.backend != "legacy1710":
            raise RuntimeError("1.7.10 backend registration is missing")
        if not callable(getattr(legacy1710_engine, "run_conversion", None)):
            raise RuntimeError("legacy 1.7.10 conversion entry point is missing")
        print(f"{APP_NAME} {__version__} packaged self-test passed")
        return 0
    except Exception as exc:
        print(f"{APP_NAME} {__version__} packaged self-test FAILED: {exc}", file=sys.stderr)
        return 70


def main() -> int:
    if "--self-test" in sys.argv:
        return packaged_self_test()

    from PySide6.QtWidgets import QApplication
    from .ui.main_window import MainWindow
    from .ui.theme import APP_STYLESHEET

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName("Wargames Development")
    app.setOrganizationDomain("wargames.dev")
    app.setStyleSheet(APP_STYLESHEET)
    win = MainWindow()
    win.show()
    return app.exec()
