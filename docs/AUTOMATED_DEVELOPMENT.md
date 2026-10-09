# SEEFIX Python Agent — Claude issue automation (opt-in)

The ChatGPT hourly reviewer inspects pull requests across SEEFIX API, Agents, Web Admin and Mobile, and opens deduplicated follow-up issues in their responsible repositories. This workflow is a **separate, optional** implementation trigger for bounded Python Agent issues.

## Set up
1. Review this draft PR and activate it only after workflow-security review and protected-main rules.
2. Configure repository GitHub Actions secret `ANTHROPIC_API_KEY`. Claude GitHub App authorization alone does not configure this key; Anthropic API charges can apply.
3. With the GitHub account `erwin-io`, add the issue label `claude-build` to a bounded Agent issue, or post an issue comment starting `@claude`. Other actors are ignored.
4. Ensure the resulting developer PR adds tests and documentation and receives independent review. Never merge automatically.

## Agent acceptance constraints
Python FastAPI worker concurrency, Qwen inspection schema, cancel guards, atomic SQL queue claims and job-status lifecycle must remain correct. AI cannot make Maintenance Review route decisions or human Work Order completion decisions. Run `python -m pytest -q tests` on suitable test environments; independent live DB/Ollama tests require separately provided development credentials/infrastructure.

Note: Opening an issue via ChatGPT does not itself start Claude; only a configured, approved GitHub event trigger does.
