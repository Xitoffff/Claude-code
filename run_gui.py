#!/usr/bin/env python3
"""Entry point for the native desktop GUI (PySide6).

    python run_gui.py

Requires PySide6 (and, recommended, curl_cffi for live scraping):

    pip install -r requirements.txt
"""

from fiverr_parser.gui import main

if __name__ == "__main__":
    main()
