Create a planner agent that is supposed to run before human_approal interrupt and after the diagnosis agent.
Create this in planner_agent.py and similar style of using ReAct loop like diagnosis_agent and remediation_agent.

Functionality:

- take in the root cause and the issue of the cluster from the diagnosis agent
- search and see the repo (make sure latest change) to generate plan on what the fix should be (this plan is to be delegated to the remediation agent). For the git setup please refer to how remediation_agent sets git repo up.
- This is a read only agent, the fixes are performed by the diagnosis agent

Tools:

- git related tools to setup the git repo
- file (read) tools to read the repo and perform agentic search

Output:

- A new model that outputs the remediation steps, please enhance as you like here. Each steps produce must be clear and detailed in which file does it change, the old and expected new output, so that the agentic search will be much faster for the remediation agent.

After fixing this, please modify the nodes.py and builder.py
