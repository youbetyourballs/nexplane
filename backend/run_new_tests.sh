#!/bin/sh
python -m pytest app/tests/test_aws_backup_actions.py app/tests/test_scheduler_jobs.py -x -q --no-header 2>&1
echo "EXIT_CODE:$?"
