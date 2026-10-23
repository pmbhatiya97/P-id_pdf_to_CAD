import json
import sys
from pathlib import Path
from lineaforge.desktop import main

if __name__ == "__main__":
    if "--smoke-check" in sys.argv:
        from PySide6.QtWidgets import QApplication
        from lineaforge.desktop import MainWindow
        app = QApplication(sys.argv)
        window = MainWindow()
        window.show()
        app.processEvents()
        destination = Path(sys.argv[sys.argv.index("--smoke-check")+1])
        destination.write_text(json.dumps({"window_title":window.windowTitle(),"status":"started"}),encoding="utf-8")
        window.close()
    else:
        main()
