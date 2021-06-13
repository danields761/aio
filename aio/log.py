import sys


def log(msg):
    print(f"INFO: {msg}")


def log_exc(msg, exc):
    import sys
    import traceback

    print(f"ERROR: {msg}", file=sys.stderr)
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
