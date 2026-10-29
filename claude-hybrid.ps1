# Claude Code with glm-5.3:cloud as main model and qwen3.8:27b on the RTX 5090 for subagents.
# Both models are served by the 5090's Ollama; see PLAN.md phase 3.
# Usage: .\claude-hybrid.ps1 [any claude arguments]
$ErrorActionPreference = 'Stop'

$settings_file = Join-Path $PSScriptRoot 'hybrid.settings.json'
if (-not (Test-Path $settings_file)) {
    Write-Error "claude-hybrid.ps1: expected settings at $settings_file, not found"
    exit 1
}

# The global settings force subagents to sonnet; --settings ranks above user settings and undoes that.
# Built-in Explore ignores CLAUDE_CODE_SUBAGENT_MODEL, so it is overridden by name and pinned to haiku (= qwen).
$agents = @{
    Explore = @{
        description = 'Read-only codebase search. Use for broad file and code lookups.'
        prompt      = 'You are a read-only search agent. Find what was asked for and report file paths and line numbers. Never edit files.'
        tools       = @('Glob', 'Grep', 'Read', 'Bash')
        model       = 'haiku'
    }
} | ConvertTo-Json -Depth 5 -Compress

Write-Host "claude-hybrid.ps1: starting claude with $settings_file, main glm-5.3:cloud, subagents qwen3.8:27b"
claude --settings $settings_file --agents $agents @args
exit $LASTEXITCODE
