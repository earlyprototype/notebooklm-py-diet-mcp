"""Synthetic implementation called by the installed notebooklm console script."""

import os
import sys
import time
from pathlib import Path


def confirm_in_console():
    import win32con
    import win32console

    # Only send a keystroke to the login interpreter's own console.
    assert list(win32console.GetConsoleProcessList()) == [os.getpid()]
    enter = win32console.PyINPUT_RECORDType(win32console.KEY_EVENT)
    enter.KeyDown = True
    enter.RepeatCount = 1
    enter.VirtualKeyCode = win32con.VK_RETURN
    enter.Char = "\r"
    win32console.GetStdHandle(win32console.STD_INPUT_HANDLE).WriteConsoleInput([enter])
    assert input("Press Enter after signing in: ") == ""


def main():
    assert sys.argv[1:] == ["login"]
    profile = Path(os.environ["NOTEBOOKLM_HOME"])
    read_input = os.environ.get("TEST_LOGIN_READ_INPUT") == "1"
    if read_input:
        if sys.platform == "win32" and sys.stdin.isatty():
            confirm_in_console()
            (profile / "confirmed").touch()
        elif sys.platform != "win32" and not sys.stdin.isatty():
            assert os.path.samestat(os.fstat(0), os.stat(os.devnull))

    marker = profile / "starting"
    marker.write_text(str(os.getpid()), encoding="ascii")
    marker.replace(profile / "login.pid")
    if read_input and sys.stdin.readline():
        (profile / "unexpected-input").touch()
        return 1
    time.sleep(30)


if __name__ == "__main__":
    sys.exit(main())
