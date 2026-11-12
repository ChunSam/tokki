import sys

from . import VERSION

# Answered before GTK is imported, so packaging checks can run headless.
if "--version" in sys.argv[1:]:
    print(f"Tokki {VERSION}")
    sys.exit(0)

from .app import main  # noqa: E402

sys.exit(main())
