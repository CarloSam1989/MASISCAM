#!/usr/bin/env python
import os
import sys
from pathlib import Path

if __name__ == "__main__":
    project_dir = Path(__file__).resolve().parent
    os.chdir(project_dir)
    sys.path.insert(0, str(project_dir))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
