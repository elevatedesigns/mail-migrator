"""Entry point used by PyInstaller and for running from source: python run_app.py"""

import sys

from mailmigrator.app import main

if __name__ == "__main__":
    sys.exit(main())
