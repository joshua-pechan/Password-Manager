"""
Register or remove the password manager as a Windows logon startup task.

Usage:
    python startup.py           # register
    python startup.py --remove  # unregister
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TASK_NAME = "PasswordManager"


def register() -> None:
    script_dir = Path(__file__).resolve().parent
    main_script = script_dir / "main.py"

    # pythonw.exe runs without a console window; fall back to python.exe if absent
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = Path(sys.executable)

    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Local password manager API server</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Hidden>true</Hidden>
  </Settings>
  <Actions>
    <Exec>
      <Command>{pythonw}</Command>
      <Arguments>"{main_script}"</Arguments>
      <WorkingDirectory>{script_dir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>"""

    xml_path = script_dir / "_startup_task.xml"
    xml_path.write_text(xml, encoding="utf-16")

    try:
        result = subprocess.run(
            ["schtasks", "/create", "/tn", TASK_NAME, "/xml", str(xml_path), "/f"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"Error: {result.stderr.strip()}")
            sys.exit(1)
        print(f"Startup task '{TASK_NAME}' registered.")
        print("The password manager will start automatically at next logon.")
        print(f"\nTo start it now: python \"{main_script}\"")
    finally:
        xml_path.unlink(missing_ok=True)


def unregister() -> None:
    result = subprocess.run(
        ["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Error: {result.stderr.strip()}")
        sys.exit(1)
    print(f"Startup task '{TASK_NAME}' removed.")


if __name__ == "__main__":
    if "--remove" in sys.argv:
        unregister()
    else:
        register()
