#requires -Version 7.0
<#
Is a local model good enough as the SUBAGENT of a cloud main model? Information-gathering tasks through OpenCode.

Why OpenCode and not Claude Code: Claude Code takes ONE `ANTHROPIC_BASE_URL`, so a cloud main model on Ollama and a
local subagent on another server (SGLang) need a routing proxy. OpenCode 1.x takes a provider per `baseURL` and a
model per agent (`agent.explore.model`) [DOCUMENTED opencode docs v1, agents.mdx "Model"], so the split is config.
What it measures is the model in the subagent seat, not Claude Code's Explore prompt.

The tasks are read-only questions about a frozen clone of the NoLlama repo (nollama.py alone is ~7k lines), the kind
of lookup a subagent is used for. Every answer is checked against ground truth COMPUTED from that clone at run time
(grep, git log), never against a transcript and never against a hand-written answer that can go stale.

    .\probe-subagent.ps1 -Config inkling -SubUrl http://<5090>:30000/v1 -SubModelId thinkingmachines/Inkling-2-Mini
    .\probe-subagent.ps1 -Config qwen    -SubUrl http://<5090>:11434/v1 -SubModelId qwen3.8:27b

In: the main model on an Ollama OpenAI endpoint ($env:OLLAMA_5090_URL by default), the subagent model on any OpenAI
endpoint. Out: one line per task, results/subagent-probe-<config>.jsonl, transcripts and subagent exports in
results/subagent-probe/. Criteria are printed before the run so the verdict cannot be written afterwards.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $Config,
    [Parameter(Mandatory)] [string] $SubUrl,
    [Parameter(Mandatory)] [string] $SubModelId,
    [int] $SubContext = 65536,
    # Defaults to $env:OLLAMA_5090_URL so the host address never lives in this public repo.
    [string] $MainUrl = $(if ($env:OLLAMA_5090_URL) { "$($env:OLLAMA_5090_URL.TrimEnd('/'))/v1" } else { '' }),
    [string] $MainModelId = 'glm-5.3:cloud',
    [string] $SourceRepo = (Join-Path $PSScriptRoot '..\NoLlama'),
    [string] $Commit = '6ad999b',
    [int] $TimeoutSec = 900,
    [string] $WorkDir = (Join-Path ([System.IO.Path]::GetTempPath()) 'subagent-probe'),
    [string[]] $Only = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function New-RepoFixture {
    # Clone the source repo at a pinned commit into the workdir, history included.
    # Why a clone and not `git archive`: the history task needs `git log`, and a pinned commit keeps the ground truth
    # identical between configs run on different days.
    # In: source repo path, commit, target dir. Out: nothing; throws if the commit does not exist.
    param([string]$Source, [string]$Rev, [string]$Dir)
    Remove-Item -Recurse -Force $Dir -ErrorAction SilentlyContinue
    git clone -q --no-hardlinks --no-checkout $Source $Dir
    git -C $Dir checkout -q $Rev
    if ($LASTEXITCODE -ne 0) { throw "commit $Rev not found in $Source" }
}

function Get-GroundTruth {
    # Compute every task's answer from the fixture itself.
    # Why: a hand-written answer goes stale the moment the repo moves; a computed one is right for the pinned commit
    # by construction, and printing it before the run keeps the verdict honest.
    # In: the fixture dir. Out: an object with the expected values; throws if a pattern finds nothing, because an
    # empty truth would pass every answer.
    param([string]$Dir)
    $src = Get-Content (Join-Path $Dir 'nollama.py') -Raw
    $lines = Get-Content (Join-Path $Dir 'nollama.py')
    $opt_line = ($lines | Select-String -Pattern '^NEEDS_OPTIMUM\s*=' | Select-Object -First 1).LineNumber
    $opt_names = [regex]::Matches(($lines[$opt_line - 1]), '"([a-z0-9_]+)"') | ForEach-Object { $_.Groups[1].Value }
    $routes = [regex]::Matches($src, '@\w+\.(?:route|get|post|put|delete)\(\s*["''](/[^"'']*)') |
        ForEach-Object { $_.Groups[1].Value } | Where-Object { $_ -ne '/' } | Sort-Object -Unique
    $flags = [regex]::Matches($src, 'add_argument\(\s*"(--[a-z0-9-]+)"') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
    $docs = (Get-Content (Join-Path $Dir 'docs\API.md') -Raw) + (Get-Content (Join-Path $Dir 'README.md') -Raw)
    $undocumented = @($flags | Where-Object { $docs -notmatch ([regex]::Escape($_) + '(?![a-z0-9-])') })
    $first = git -C $Dir log --reverse --format=%h -S 'NEEDS_OPTIMUM = {' -- nollama.py | Select-Object -First 1
    if (-not $opt_line -or -not $opt_names -or -not $routes -or -not $flags -or -not $first) { throw 'ground truth incomplete' }
    [PSCustomObject]@{
        OptLine = $opt_line; OptNames = @($opt_names); Routes = @($routes); Flags = @($flags)
        Undocumented = $undocumented; FirstCommit = $first
    }
}

function Write-OpenCodeConfig {
    # Two providers, main model global, subagent model on both built-in subagents (explore, general).
    # Why both: the main model picks which subagent to start; pinning only explore would let it route around the test.
    # In: fixture dir. Out: opencode.json in it (untracked; excluded from the rule check).
    param([string]$Dir)
    $cfg = [ordered]@{
        '$schema' = 'https://opencode.ai/config.json'
        provider  = [ordered]@{
            main = @{ npm = '@ai-sdk/openai-compatible'; name = 'main'
                options = @{ baseURL = $MainUrl; chunkTimeout = 1800000; headerTimeout = 1800000 }
                models = @{ $MainModelId = @{ limit = @{ context = 128000; output = 16384 } } } }
            sub  = @{ npm = '@ai-sdk/openai-compatible'; name = 'subagent'
                options = @{ baseURL = $SubUrl; chunkTimeout = 1800000; headerTimeout = 1800000 }
                models = @{ $SubModelId = @{ limit = @{ context = $SubContext; output = 16384 } } } }
        }
        model     = "main/$MainModelId"
        agent     = @{ explore = @{ model = "sub/$SubModelId" }; general = @{ model = "sub/$SubModelId" } }
    }
    $cfg | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $Dir 'opencode.json') -Encoding utf8
}

function Get-SubagentStats {
    # Export one subagent session and reduce it to the numbers the evaluation needs.
    # Why export: `opencode run --format json` streams only the parent session; the child's steps, tokens and
    # reasoning live in its own session [OBSERVED 2026-11-21, trial run].
    # In: session id, fixture dir. Out: steps, peak input (context high-water mark), output and reasoning tokens,
    # tool calls, tool errors, model, seconds; $null if the export cannot be parsed (recorded as missing, not zero).
    param([string]$SessionId, [string]$Dir)
    Push-Location $Dir
    $saved = $env:PWD; $env:PWD = $Dir
    $raw = opencode export $SessionId 2>$null | Out-String
    $env:PWD = $saved
    Pop-Location
    $start = $raw.IndexOf('{')
    if ($start -lt 0) { return $null }
    $export = $raw.Substring($start) | ConvertFrom-Json
    $assistant = @($export.messages | Where-Object { $_.info.role -eq 'assistant' })
    $tools = @($assistant | ForEach-Object { $_.parts } | Where-Object { $_.type -eq 'tool' })
    $first = ($export.messages | Select-Object -First 1).info.time.created
    $last = ($assistant | Select-Object -Last 1).info.time.completed
    [PSCustomObject]@{
        session = $SessionId; model = ($assistant | Select-Object -First 1).info.modelID; steps = $assistant.Count
        peak_input = ($assistant | ForEach-Object { $_.info.tokens.input } | Measure-Object -Maximum).Maximum
        output = ($assistant | ForEach-Object { $_.info.tokens.output } | Measure-Object -Sum).Sum
        reasoning = ($assistant | ForEach-Object { $_.info.tokens.reasoning } | Measure-Object -Sum).Sum
        max_step_reasoning = ($assistant | ForEach-Object { $_.info.tokens.reasoning } | Measure-Object -Maximum).Maximum
        tool_calls = $tools.Count; tool_errors = @($tools | Where-Object { $_.state.status -eq 'error' }).Count
        # OpenCode compacts at context limit minus output reserve (65536 - 16384 = 49152 for Inkling's recipe): the
        # count is how often a task did not fit [OBSERVED 2026-11-21, peak_in stopped at ~49K].
        compactions = @($export.messages | ForEach-Object { $_.parts } | Where-Object { $_.type -eq 'compaction' }).Count
        seconds = if ($first -and $last) { [int](($last - $first) / 1000) } else { $null }
        export = $export
    }
}

function Invoke-Task {
    # Run one question with `opencode run --format json`, then judge the final answer against computed truth.
    # In: label, prompt, a scriptblock taking the answer text and returning @{ ok; detail }. Out: a result object.
    # A timeout is its own verdict: "still going" is a different fact from "wrong".
    param([string]$Label, [string]$Prompt, [scriptblock]$Judge)
    $log = Join-Path $WorkDir "$Label.jsonl"
    $stdin = Join-Path $WorkDir '.probe-stdin'
    Set-Content -Path $stdin -Value '' -NoNewline
    # Launcher rules from ..\NoLlama\scripts\agent-probe.ps1: stdin from an empty file, PWD pointed at the fixture,
    # every argument quoted by hand for Start-Process [OBSERVED 2026-10-07 and 2026-10-29].
    $cmd = Get-Command opencode -ErrorAction Stop
    $oc_args = @('run', '--format', 'json', '--auto', $Prompt) | ForEach-Object { '"' + $_ + '"' }
    $file, $argv = switch -Wildcard ($cmd.Source) {
        '*.ps1' { 'pwsh', (@('-NoProfile', '-File', ('"' + $cmd.Source + '"')) + $oc_args) }
        '*.cmd' { 'cmd.exe', (@('/c', $cmd.Source) + $oc_args) }
        default { $cmd.Source, $oc_args }
    }
    Write-Host "  $Label : running"
    $saved = $env:PWD; $env:PWD = $WorkDir
    $started = Get-Date
    $proc = Start-Process -FilePath $file -ArgumentList $argv -WorkingDirectory $WorkDir -NoNewWindow -PassThru `
        -RedirectStandardInput $stdin -RedirectStandardOutput $log -RedirectStandardError "$log.err"
    $env:PWD = $saved
    Wait-Process -Id $proc.Id -Timeout $TimeoutSec -ErrorAction SilentlyContinue
    $timed_out = $false
    if (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue) { Stop-Process -Id $proc.Id -Force; $timed_out = $true }
    $seconds = [int]((Get-Date) - $started).TotalSeconds

    $events = @(Get-Content $log -ErrorAction SilentlyContinue | Where-Object { $_.StartsWith('{') } |
        ForEach-Object { $_ | ConvertFrom-Json -ErrorAction SilentlyContinue } | Where-Object { $_ })
    $answer = ($events | Where-Object { $_.type -eq 'text' } | ForEach-Object { $_.part.text }) -join "`n"
    $main_tokens = @($events | Where-Object { $_.type -eq 'step_finish' } | ForEach-Object { $_.part.tokens })
    $task_parts = @($events | Where-Object { $_.type -eq 'tool_use' -and $_.part.tool -eq 'task' })
    # Unique: the stream can carry the same task part twice, and counting it twice doubled a session's numbers
    # [OBSERVED 2026-11-21, inkling run 2, flags].
    $subs = @($task_parts | ForEach-Object { $_.part.state.metadata.sessionId } | Where-Object { $_ } | Sort-Object -Unique |
        ForEach-Object { Get-SubagentStats -SessionId $_ -Dir $WorkDir } | Where-Object { $_ })
    $sub_models = @($subs | ForEach-Object { $_.model } | Sort-Object -Unique)

    # Rule following: read-only task, so the tracked tree must be untouched.
    $dirty = @(git -C $WorkDir status --porcelain --untracked-files=no 2>$null)
    $judged = if ($timed_out) { @{ ok = $false; detail = 'timeout' } } else { & $Judge $answer }
    $verdict = if ($timed_out) { 'TIMEOUT' }
               elseif ($subs.Count -eq 0) { 'NO-SUBAGENT' }
               elseif ($sub_models -notcontains $SubModelId) { 'WRONG-MODEL' }
               elseif ($dirty.Count -gt 0) { 'EDITED' }
               elseif ($judged.ok) { 'PASS' } else { 'FAIL' }
    [PSCustomObject]@{
        Task = $Label; Verdict = $verdict; Seconds = $seconds; Detail = $judged.detail; Answer = $answer
        Dirty = $dirty; Subs = $subs; MainSteps = $main_tokens.Count
        MainInput = ($main_tokens | ForEach-Object { $_.input } | Measure-Object -Sum).Sum
        MainOutput = ($main_tokens | ForEach-Object { $_.output } | Measure-Object -Sum).Sum
        FileLineRefs = ([regex]::Matches($answer, '[\w./\\-]+\.(?:py|md|ps1):\d+')).Count
        Log = $log
    }
}

# --- run -------------------------------------------------------------------
if (-not $MainUrl) { Write-Host 'ERROR: no main endpoint. Pass -MainUrl or set OLLAMA_5090_URL.' -ForegroundColor Red; exit 2 }
if (-not (Get-Command opencode -ErrorAction SilentlyContinue)) { Write-Host 'ERROR: opencode is not on PATH' -ForegroundColor Red; exit 2 }

New-RepoFixture -Source (Resolve-Path $SourceRepo) -Rev $Commit -Dir $WorkDir
Write-OpenCodeConfig -Dir $WorkDir
$truth = Get-GroundTruth -Dir $WorkDir

Write-Host '=== subagent probe ===' -ForegroundColor Cyan
Write-Host "  main     : $MainModelId at $MainUrl"
Write-Host "  subagent : $SubModelId at $SubUrl (explore + general), context limit $SubContext"
Write-Host "  fixture  : $SourceRepo at $Commit"
Write-Host '  PASS     : a subagent on the subagent model did the work, no tracked file changed, and the answer has:' -ForegroundColor DarkGray
Write-Host "    optimum  : $($truth.OptNames -join ',') and nollama.py:$($truth.OptLine)" -ForegroundColor DarkGray
Write-Host '    tuning   : _apply_tuning and the cap (repetition_penalty_max or model-tuning.json)' -ForegroundColor DarkGray
Write-Host "    routes   : all $($truth.Routes.Count) non-root routes" -ForegroundColor DarkGray
Write-Host "    flags    : all $($truth.Undocumented.Count) undocumented flags ($($truth.Undocumented -join ' ')), none of the documented ones" -ForegroundColor DarkGray
Write-Host "    history  : commit $($truth.FirstCommit)" -ForegroundColor DarkGray
Write-Host '  (criteria printed before the run on purpose)' -ForegroundColor DarkGray
Write-Host ''

$tasks = [ordered]@{
    optimum = @{
        Prompt = 'Use the explore subagent to find out which model architectures nollama.py forces onto the optimum backend, and where in the code that is decided. Answer with the architecture names and file:line references. Do not edit any file.'
        Judge  = { param($a) $hit = @($truth.OptNames | Where-Object { $a -match [regex]::Escape($_) }).Count -eq $truth.OptNames.Count -and $a -match "nollama\.py:$($truth.OptLine)\b"
                   @{ ok = $hit; detail = "names+line $(if ($hit) { 'found' } else { 'missing' })" } }
    }
    tuning = @{
        Prompt = 'Use the explore subagent to trace what happens to a repetition_penalty sent by a client before it reaches openvino_genai in nollama.py: which function changes it, under what condition, and why. Answer with the function name and file:line references. Do not edit any file.'
        # The cap lives in model-tuning.json under repetition_penalty_max; naming either is naming the cap. The first
        # judge demanded the literal key and failed an answer that named the file, the function and two more
        # functions the truth did not know about [OBSERVED 2026-11-21, inkling run 2].
        Judge  = { param($a) $hit = $a -match '_apply_tuning' -and $a -match 'repetition_penalty_max|model-tuning\.json'
                   @{ ok = $hit; detail = "function+cap $(if ($hit) { 'found' } else { 'missing' })" } }
    }
    routes = @{
        Prompt = 'Use the explore subagent to list every HTTP route the nollama.py server exposes, with its HTTP method(s). Answer as a list of paths. Do not edit any file.'
        Judge  = { param($a) $found = @($truth.Routes | Where-Object { $a -match ([regex]::Escape($_) + '(?![\w/-])') })
                   @{ ok = $found.Count -eq $truth.Routes.Count; detail = "$($found.Count)/$($truth.Routes.Count) routes" } }
    }
    flags = @{
        Prompt = 'Use the explore subagent to find which command-line flags defined in nollama.py (argparse add_argument) are NOT mentioned anywhere in docs/API.md or README.md. Answer with the list of undocumented flags only. Do not edit any file.'
        Judge  = { param($a) $named = @([regex]::Matches($a, '--[a-z0-9][a-z0-9-]*') | ForEach-Object { $_.Value } | Sort-Object -Unique)
                   $hits = @($truth.Undocumented | Where-Object { $named -contains $_ })
                   $false_pos = @($named | Where-Object { $truth.Flags -contains $_ -and $truth.Undocumented -notcontains $_ })
                   @{ ok = $hits.Count -eq $truth.Undocumented.Count -and $false_pos.Count -eq 0
                      detail = "recall $($hits.Count)/$($truth.Undocumented.Count), false positives $($false_pos.Count) $($false_pos -join ' ')" } }
    }
    history = @{
        Prompt = 'Use the explore subagent to find, with git, the commit that first introduced the NEEDS_OPTIMUM set in nollama.py. Answer with the short commit hash and its date. Do not edit any file.'
        Judge  = { param($a) $hit = $a -match [regex]::Escape($truth.FirstCommit)
                   @{ ok = $hit; detail = "commit $(if ($hit) { 'found' } else { 'missing' })" } }
    }
}

$results = @()
foreach ($name in $tasks.Keys) {
    if ($Only -and $Only -notcontains $name) { continue }
    $results += Invoke-Task -Label $name -Prompt $tasks[$name].Prompt -Judge $tasks[$name].Judge
}

$keep_dir = Join-Path $PSScriptRoot 'results' 'subagent-probe'
New-Item -ItemType Directory -Path $keep_dir -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$jsonl = Join-Path $PSScriptRoot 'results' "subagent-probe-$Config.jsonl"
Write-Host ''
foreach ($r in $results) {
    $color = switch ($r.Verdict) { 'PASS' { 'Green' } 'TIMEOUT' { 'Yellow' } default { 'Red' } }
    $sub = $r.Subs | ForEach-Object { "steps=$($_.steps) peak_in=$($_.peak_input) reasoning=$($_.reasoning) tool_err=$($_.tool_errors)/$($_.tool_calls) compact=$($_.compactions) $($_.seconds)s" }
    Write-Host ('  {0,-8} {1,-11} {2,4}s  subagents={3}  {4}  | {5}' -f $r.Task, $r.Verdict, $r.Seconds, $r.Subs.Count, ($sub -join ' ; '), $r.Detail) -ForegroundColor $color
    $dest = Join-Path $keep_dir "$stamp-$Config-$($r.Task)"
    Copy-Item $r.Log "$dest.jsonl" -Force
    if (Test-Path "$($r.Log).err") { Copy-Item "$($r.Log).err" "$dest.jsonl.err" -Force }
    $i = 0
    foreach ($s in $r.Subs) { $s.export | ConvertTo-Json -Depth 30 | Set-Content "$dest.sub$i.json" -Encoding utf8; $i++ }
    [PSCustomObject]@{
        when = $stamp; config = $Config; main = $MainModelId; subagent = $SubModelId; commit = $Commit; task = $r.Task
        verdict = $r.Verdict; seconds = $r.Seconds; detail = $r.Detail; file_line_refs = $r.FileLineRefs
        main_steps = $r.MainSteps; main_input = $r.MainInput; main_output = $r.MainOutput
        subagents = @($r.Subs | Select-Object session, model, steps, peak_input, output, reasoning, max_step_reasoning, tool_calls, tool_errors, compactions, seconds)
        dirty = $r.Dirty; answer = $r.Answer; log = "$dest.jsonl"
    } | ConvertTo-Json -Depth 6 -Compress | Add-Content -Path $jsonl -Encoding utf8
}
$passed = @($results | Where-Object Verdict -eq 'PASS').Count
Write-Host ''
Write-Host "  $passed/$($results.Count) passed for $Config  ->  $jsonl" -ForegroundColor Cyan
exit $(if ($passed -eq $results.Count) { 0 } else { 1 })
