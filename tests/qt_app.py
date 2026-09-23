"""One QGuiApplication for every Qt test in the run.

Qt allows a single application object per process, and QML needs a
QGuiApplication; a plain QCoreApplication created by an earlier test makes
later QML tests crash.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def application():
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    assert isinstance(app, QGuiApplication), "a QCoreApplication was created before the GUI one"
    return app
