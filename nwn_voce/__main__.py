"""Avvio da sorgente:  python -m nwn_voce
Lettore video (processo separato):  python -m nwn_voce --video <file>"""

import sys

if len(sys.argv) >= 3 and sys.argv[1] == "--video":
    from nwn_voce.cinema import main_video
    sys.exit(main_video(sys.argv[2:]))

from nwn_voce.main import main

main()
