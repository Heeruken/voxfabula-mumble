"""Avvio da sorgente:  python -m nwn_voce
Lettore video (processo separato):  python -m nwn_voce --video <file>
Overlay del palco (processo separato):  python -m nwn_voce --palco <porta> <segreto>"""

import sys

if len(sys.argv) >= 4 and sys.argv[1] == "--palco":
    from nwn_voce.palco import main_palco
    sys.exit(main_palco(sys.argv[2:]))

if len(sys.argv) >= 3 and sys.argv[1] == "--video":
    from nwn_voce.cinema import main_video
    sys.exit(main_video(sys.argv[2:]))

from nwn_voce.main import main

main()
