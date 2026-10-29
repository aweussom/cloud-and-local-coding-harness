# Claude Code with glm-5.3:cloud as main model and qwen3.8:27b on the RTX 5090 for subagents.
# Both models are served by the 5090's Ollama, at $env:OLLAMA_5090_URL; see brain/plans/PLAN.md phase 3.
# Usage: .\claude-hybrid.ps1 [any claude arguments]
$ErrorActionPreference = 'Stop'

$base_url = $env:OLLAMA_5090_URL
if (-not $base_url) {
    Write-Error 'claude-hybrid.ps1: OLLAMA_5090_URL is not set. Expected something like http://<host>:11434'
    exit 1
}

# Settings are built here, not kept in a file, so the host address never lives in this public repo.
# --settings ranks above ~/.claude/settings.json, which forces subagents to sonnet; this undoes that.
# Subagents reach qwen through the haiku alias.
$settings_file = Join-Path ([System.IO.Path]::GetTempPath()) 'claude-hybrid.settings.json'
@{
    env   = @{
        ANTHROPIC_BASE_URL                   = $base_url
        ANTHROPIC_AUTH_TOKEN                 = 'ollama'
        ANTHROPIC_API_KEY                    = ''
        ANTHROPIC_MODEL                      = 'glm-5.3:cloud'
        ANTHROPIC_DEFAULT_OPUS_MODEL         = 'glm-5.3:cloud'
        ANTHROPIC_DEFAULT_SONNET_MODEL       = 'glm-5.3:cloud'
        ANTHROPIC_DEFAULT_HAIKU_MODEL        = 'qwen3.8:27b'
        CLAUDE_CODE_SUBAGENT_MODEL           = 'haiku'
        CLAUDE_CODE_SUBAGENT_MODEL_FORCE     = '0'
        CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS = '2'
    }
    model = 'glm-5.3:cloud'
} | ConvertTo-Json -Depth 5 | Set-Content -Path $settings_file -Encoding utf8

# Built-in Explore ignores CLAUDE_CODE_SUBAGENT_MODEL, so it is overridden by name and pinned to haiku (= qwen).
$agents = @{
    Explore = @{
        description = 'Read-only codebase search. Use for broad file and code lookups.'
        prompt      = 'You are a read-only search agent. Find what was asked for and report file paths and line numbers. Never edit files.'
        tools       = @('Glob', 'Grep', 'Read', 'Bash')
        model       = 'haiku'
    }
} | ConvertTo-Json -Depth 5 -Compress

Write-Host "claude-hybrid.ps1: starting claude against $base_url, main glm-5.3:cloud, subagents qwen3.8:27b"
claude --settings $settings_file --agents $agents @args
exit $LASTEXITCODE
