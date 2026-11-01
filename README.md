1. Full Autonomous Startup Execution (main.py):

python main.py --reset --requirement "solve: 1+1 arithmetic evaluator"

[STARTUP] Initializing new autonomous run for project 'vibe-app'...
======================================================================
  ORCHESTRATION PIPELINE STARTING (LangGraph Engine)
======================================================================
  Final Phase:     COMPLETED
  Completed Tasks: ['architecture', 'code', 'devops', 'qa']
  Total Files:     4
  Retries Used:    0
  Checkpoint:      logs/company_state.json
  Test Status:     PASSED: All tests succeeded.

[SUCCESS] Project built and verified successfully by Autonomous Startup!

2. Self-Correction on Simulated QA Failure:

python main.py --reset --simulate-bug --requirement "solve: 1+1 arithmetic evaluator"
  Final Phase:     COMPLETED
  Completed Tasks: ['architecture', 'devops', 'code', 'qa']
  Retries Used:    1 (Self-corrected via QA traceback loop)
  Test Status:     PASSED: All tests succeeded.

  
3. Resume-on-Failure Test:
Tested by injecting an interrupted state (current_phase: CODER, completed_tasks: ['architecture']):

powershell
python main.py --resume
[RESUME] Found existing run for project 'vibe-app'.
    Last Phase: CODER
    Completed Tasks: ['architecture']
    Resuming strictly from incomplete phase: CODER
  Completed Tasks: ['architecture', 'code', 'devops', 'qa']