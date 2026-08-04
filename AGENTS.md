# AI Agent Execution Rules

## Environment & Tool Execution
- Ensure shell commands and scripts target the correct environment (e.g. Hub vs Edge).
- Use `.\hub\setup_hub.ps1` to manage the `geniex-env` virtual environment rather than manually running `pip` for core dependencies.
- When adding Python dependencies, prefer pure Python libraries or those with pre-compiled wheels to avoid native C-extension compilation failures across different architectures.

## Commit Message Standards
- Use the imperative mood (e.g., "Add", "Fix", "Refactor").
- Start the subject line with a capitalized letter.
- Do not end the summary line with a period.
- Keep the summary line under 72 characters.
- Separate the subject from the body with a blank line.
- Use the body to explain *what* and *why* the change was made. Use clear bullet points for the notes, wrapping text at 72 characters.
- In the end of the commit message body, always append the AI assistant along with harness if needed: `*Gemini 3.1 Pro (Antigravity IDE) by Google Deepmind*`
- Example: `Add orientation-aware robot turning fallback`

## Code Placement & Modification Rules
- Do NOT add application-specific business logic or hardcoded rules into the `hub/framework/` files; it must remain use-case agnostic.
- Contain all new features or use-cases entirely within a new directory under `hub/apps/<app_name>/`.
- Strictly subclass `framework.policy.Policy` and implement the `evaluate` method when building a new application policy.

