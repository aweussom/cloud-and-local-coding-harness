#requires -Version 7.0
<#
Can this model setup actually drive Claude Code? Ported from ..\nollama\scripts\agent-probe.ps1 (OpenCode).

Builds a throwaway project with two failing tests, runs `claude -p` against the 5090's Ollama, and checks whether
the code was fixed. Pass/fail is decided by running the ORIGINAL tests, not by reading the transcript: a model
that narrates tool use and hands the work back sounds exactly like one that did it.

    .\probe-agent.ps1 -MainModel glm-5.3:cloud -SubagentModel qwen3.8:27b      # hybrid (config B)
    .\probe-agent.ps1 -MainModel glm-5.3:cloud -SubagentModel glm-5.3:cloud    # all cloud (config A)
    .\probe-agent.ps1 -MainModel qwen3.8:27b   -SubagentModel qwen3.8:27b      # all local

Three tasks:
  fix       - main model fixes a percentage bug.
  feature   - main model adds apply_tax() and a test for it.
  delegate  - main model must hand the fix to a general-purpose subagent. Only this one exercises the split.

Runs with the user's real ~/.claude setup (global CLAUDE.md, hooks), because that is what daily use looks like.
The --settings file overrides model routing only.

Out: one line per task, a JSONL file in results/, transcripts in results/agent-probe/, exit 0 only on a clean sweep.
Criteria are printed before the run so the verdict cannot be written afterwards.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $MainModel,
    [Parameter(Mandatory)] [string] $SubagentModel,
    # Defaults to $env:OLLAMA_5090_URL so the host address never lives in this public repo.
    [string] $BaseUrl = $env:OLLAMA_5090_URL,
    [string] $Config = '',
    [int] $TimeoutSec = 900,
    [string] $WorkDir = (Join-Path ([System.IO.Path]::GetTempPath()) 'claude-agent-probe'),
    [switch] $KeepWorkdir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-LfFile {
    # Write text with LF line endings only, UTF-8 without a BOM.
    # Why: Set-Content ends a file with CRLF on Windows, and a mixed file is what an exact-match edit tool trips on.
    param([Parameter(ValueFromPipeline)][string]$Text, [string]$Path)
    (($Text -replace "`r`n", "`n") + "`n") | Set-Content -Path $Path -NoNewline -Encoding utf8
}

function New-Fixture {
    # Write the project the model has to repair: a two-character percentage bug, impossible to fix by accident.
    # Line endings pinned to LF in the files AND the fixture repo; autocrlf=true rewrote edited files as CRLF on
    # reset and broke exact-match edits [OBSERVED 2026-09-24 in nollama].
    param([string]$Dir)
    Remove-Item -Recurse -Force $Dir -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path (Join-Path $Dir 'tests') -Force | Out-Null
    @'
"""A tiny expression helper used by the billing report."""


def apply_discount(amount, percent):
    """Return amount with percent taken off, rounded to two decimals."""
    return round(amount - (amount * percent), 2)


def total(items):
    """Sum a list of (amount, discount_percent) pairs."""
    return round(sum(apply_discount(a, p) for a, p in items), 2)
'@ | Write-LfFile -Path (Join-Path $Dir 'calc.py')
    @'
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from calc import apply_discount, total


def test_apply_discount_takes_a_percentage_not_a_fraction():
    assert apply_discount(200.0, 10) == 180.0


def test_total_sums_discounted_items():
    assert total([(200.0, 10), (100.0, 50)]) == 230.0


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("ok  ", name)
            except AssertionError as e:
                fails += 1; print("FAIL", name, e)
    raise SystemExit(1 if fails else 0)
'@ | Write-LfFile -Path (Join-Path $Dir 'tests\test_calc.py')
    Push-Location $Dir
    git init -q 2>$null; git config core.autocrlf false; git add -A 2>$null
    git -c user.email=probe@local -c user.name=probe commit -qm fixture 2>$null
    Pop-Location
}

function New-BillingFixture {
    # A multi-file package where the failure shows in report.py but the bug lives in currency.py (divides where it
    # must multiply). Why this shape: it separates a model that traces a call chain from one that patches where the
    # symptom is. Trap: inverting the EUR rate in rates.py also makes the visible test pass; the hidden test
    # (written outside the workdir, never shown to the model) catches that and any other workaround.
    param([string]$Dir, [string]$HiddenTest)
    Remove-Item -Recurse -Force $Dir -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path (Join-Path $Dir 'billing'), (Join-Path $Dir 'tests') -Force | Out-Null
    '' | Write-LfFile -Path (Join-Path $Dir 'billing\__init__.py')
    @'
"""Exchange rates against NOK, as published by the bank each morning."""

# NOK per one unit of the foreign currency.
NOK_PER_UNIT = {"NOK": 1.0, "EUR": 11.50, "SEK": 1.00, "USD": 10.80}


def nok_per_unit(currency):
    """How many NOK one unit of currency buys."""
    return NOK_PER_UNIT[currency]
'@ | Write-LfFile -Path (Join-Path $Dir 'billing\rates.py')
    @'
"""Currency conversion for invoice lines."""
from billing.rates import nok_per_unit


def to_nok(amount, currency):
    """Convert amount in currency to NOK, rounded to two decimals."""
    return round(amount / nok_per_unit(currency), 2)
'@ | Write-LfFile -Path (Join-Path $Dir 'billing\currency.py')
    @'
"""Invoice lines."""
from dataclasses import dataclass

from billing.currency import to_nok


@dataclass
class Line:
    description: str
    amount: float
    currency: str


def line_total_nok(line):
    """One line's amount in NOK."""
    return to_nok(line.amount, line.currency)
'@ | Write-LfFile -Path (Join-Path $Dir 'billing\invoice.py')
    @'
"""The monthly report."""
from billing.invoice import line_total_nok


def report_total(lines):
    """Total of all invoice lines, in NOK."""
    return round(sum(line_total_nok(line) for line in lines), 2)
'@ | Write-LfFile -Path (Join-Path $Dir 'billing\report.py')
    $runner = @'


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("ok  ", name)
            except AssertionError as e:
                fails += 1; print("FAIL", name, e)
    raise SystemExit(1 if fails else 0)
'@
    $header = @'
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
'@
    ($header + @'
from billing.invoice import Line
from billing.report import report_total


def test_nok_only_report():
    assert report_total([Line("hosting", 100.0, "NOK"), Line("support", 50.0, "NOK")]) == 150.0


def test_mixed_currency_report():
    assert report_total([Line("hosting", 100.0, "NOK"), Line("licence", 10.0, "EUR")]) == 215.0
'@ + $runner) | Write-LfFile -Path (Join-Path $Dir 'tests\test_report.py')
    ($header + @'
from billing.invoice import Line, line_total_nok


def test_nok_line():
    assert line_total_nok(Line("hosting", 100.0, "NOK")) == 100.0
'@ + $runner) | Write-LfFile -Path (Join-Path $Dir 'tests\test_invoice.py')
    @'
import sys
sys.path.insert(0, sys.argv[1])
from billing.rates import nok_per_unit
from billing.currency import to_nok
assert nok_per_unit("EUR") == 11.50, "rates.py was changed instead of the conversion"
assert nok_per_unit("USD") == 10.80, "rates.py was changed instead of the conversion"
assert to_nok(10.0, "USD") == 108.0, "to_nok still wrong for USD"
assert to_nok(2.0, "EUR") == 23.0, "to_nok still wrong for EUR"
print("hidden ok")
'@ | Write-LfFile -Path $HiddenTest
    Push-Location $Dir
    git init -q 2>$null; git config core.autocrlf false; git add -A 2>$null
    git -c user.email=probe@local -c user.name=probe commit -qm fixture 2>$null
    Pop-Location
}

function Write-ProbeSettings {
    # Model routing for this run, passed with --settings so it ranks above ~/.claude/settings.json,
    # which forces subagents to sonnet. Subagents go through the haiku alias (see PLAN.md phase 3).
    # Written next to the workdir, not in it, so it never shows up as a change the agent made.
    param([string]$Path)
    $settings = @{
        env   = @{
            ANTHROPIC_BASE_URL                   = $BaseUrl
            ANTHROPIC_AUTH_TOKEN                 = 'ollama'
            ANTHROPIC_API_KEY                    = ''
            ANTHROPIC_MODEL                      = $MainModel
            ANTHROPIC_DEFAULT_OPUS_MODEL         = $MainModel
            ANTHROPIC_DEFAULT_SONNET_MODEL       = $MainModel
            ANTHROPIC_DEFAULT_HAIKU_MODEL        = $SubagentModel
            CLAUDE_CODE_SUBAGENT_MODEL           = 'haiku'
            CLAUDE_CODE_SUBAGENT_MODEL_FORCE     = '0'
            CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS = '2'
        }
        model = $MainModel
    }
    $settings | ConvertTo-Json -Depth 5 | Set-Content -Path $Path -Encoding utf8
}

function Read-Stream {
    # Pull the facts out of a stream-json transcript: tool calls, permission denials, per-model token use.
    # A transcript is evidence of what was attempted; the tests are evidence of what was done.
    param([string]$Log)
    $tool_calls = 0
    $tool_names = @()
    $result = $null
    foreach ($line in (Get-Content $Log -ErrorAction SilentlyContinue)) {
        if (-not $line.StartsWith('{')) { continue }
        $event = $line | ConvertFrom-Json -ErrorAction SilentlyContinue
        if ($null -eq $event) { continue }
        if ($event.type -eq 'assistant' -and $event.message.content) {
            foreach ($block in $event.message.content) {
                if ($block.type -eq 'tool_use') { $tool_calls++; $tool_names += $block.name }
            }
        }
        if ($event.type -eq 'result') { $result = $event }
    }
    $usage = @{}
    $denials = 0
    $turns = 0
    if ($null -ne $result) {
        $turns = $result.num_turns
        if ($result.PSObject.Properties.Name -contains 'permission_denials') { $denials = @($result.permission_denials).Count }
        if ($result.PSObject.Properties.Name -contains 'modelUsage') {
            foreach ($model_entry in $result.modelUsage.PSObject.Properties) {
                $usage[$model_entry.Name] = @{ in = $model_entry.Value.inputTokens; out = $model_entry.Value.outputTokens }
            }
        }
    }
    [PSCustomObject]@{ ToolCalls = $tool_calls; Tools = ($tool_names | Select-Object -Unique) -join ','; Denials = $denials; Turns = $turns; Usage = $usage; GotResult = ($null -ne $result) }
}

function Get-FailureKind {
    # Say WHY a task failed. Heuristics over a transcript: a label to start from, not evidence.
    param([object]$Stream, [string]$Dir)
    if (-not $Stream.GotResult) { return 'no result event (harness, launcher or endpoint, not necessarily the model)' }
    if ($Stream.ToolCalls -eq 0) { return 'never called a tool -- wrote prose instead of acting' }
    if ($Stream.Denials -gt 0) { return "called tools, $($Stream.Denials) permission denials -- check the allowed-tools list before blaming the model" }
    $dirty = @(git -C $Dir status --porcelain 2>$null | Where-Object { $_ -notmatch '\.log|__pycache__' })
    if ($dirty.Count -eq 0) { return 'called tools but changed nothing' }
    return "changed $($dirty -join ', ') but verification fails -- a comprehension or instruction failure, read the .diff"
}

function Test-OriginalTests {
    # Run the fixture's ORIGINAL tests against whatever calc.py the agent left.
    # Why: rewriting the test file is a way to "make the tests pass" [OBSERVED 2026-09-23 in nollama, Qwen3-14B].
    param([string]$Dir)
    $orig = Join-Path $Dir 'tests\_probe_original.py'
    git -C $Dir show HEAD:tests/test_calc.py 2>$null | Set-Content -Path $orig -Encoding utf8
    & $py $orig *> $null
    $ok = $LASTEXITCODE -eq 0
    Remove-Item $orig -Force -ErrorAction SilentlyContinue
    return $ok
}

function Get-OllamaQuota {
    # Ollama Cloud allowance used, in percent, as the statusline shows it (..\claude-code-quota quota-lib.ps1:
    # same endpoint, same remaining->used conversion).
    # Why: `claude -p` has no statusline, and quota per task is the cost column of the comparison.
    # In: nothing; reads $env:OLLAMA_API_KEY. Out: session/weekly percent used plus the session reset time, or
    # $null when there is no key or the call fails -- recorded as missing, never as zero.
    if (-not $env:OLLAMA_API_KEY) { return $null }
    try {
        $balance = Invoke-RestMethod 'https://ollama.com/api/balance' -Headers @{ Authorization = "Bearer $env:OLLAMA_API_KEY" } -TimeoutSec 20
    } catch {
        Write-Host "  ollama balance fetch failed: $($_.Exception.Message)" -ForegroundColor Yellow
        return $null
    }
    [PSCustomObject]@{
        session_used  = [math]::Round(100 - $balance.included.session.remaining_percent, 2)
        weekly_used   = [math]::Round(100 - $balance.included.weekly.remaining_percent, 2)
        session_reset = $balance.included.session.resets_at
    }
}

function Invoke-Task {
    # Run one task with `claude -p` and decide it by running code. A timeout is its own verdict.
    param([string]$Dir, [string]$Label, [string]$Prompt, [scriptblock]$Verify, [int]$TimeoutSec)
    Push-Location $Dir
    git checkout -q . 2>$null
    git clean -fdq -e '*.log*' 2>$null
    $log = Join-Path $Dir "$Label.log"

    # Start-Process joins -ArgumentList with spaces and drops quotes: the --agents JSON arrived as
    # "Expected '}'" [OBSERVED 2026-10-29]. So agents go by file (allowed with -p) and every argument is quoted here.
    # None of the arguments contain a double quote, so wrapping is enough.
    $claude_args = @(
        '-p', $Prompt,
        '--settings', $settings_file,
        '--agents', $agents_file,
        '--output-format', 'stream-json', '--verbose',
        '--permission-mode', 'acceptEdits',
        '--allowedTools', 'Read,Edit,Write,Glob,Grep,Agent,Bash(python *),Bash(python3 *),Bash(py *)'
    ) | ForEach-Object { '"' + $_ + '"' }
    # Start-Process needs an executable; an npm .ps1/.cmd shim fails as "%1 is not a valid Win32 application".
    $cmd = Get-Command claude -ErrorAction Stop
    $file, $argv = switch -Wildcard ($cmd.Source) {
        '*.ps1' { 'pwsh', (@('-NoProfile', '-File', $cmd.Source) + $claude_args) }
        '*.cmd' { 'cmd.exe', (@('/c', $cmd.Source) + $claude_args) }
        default { $cmd.Source, $claude_args }
    }
    # stdin from an empty file and PWD pointed at the fixture: both bit `opencode run` [OBSERVED 2026-10-07 in
    # nollama]. Kept for claude too; cheaper than finding out again.
    $stdin = Join-Path $Dir '.probe-stdin'
    Set-Content -Path $stdin -Value '' -NoNewline
    $saved_pwd = $env:PWD; $env:PWD = $Dir
    Write-Host "  $Label : trying claude -p, expecting the original tests to pass afterwards"
    $quota_before = Get-OllamaQuota
    $started = Get-Date
    $proc = Start-Process -FilePath $file -ArgumentList $argv -WorkingDirectory $Dir -NoNewWindow -PassThru `
        -RedirectStandardInput $stdin -RedirectStandardOutput $log -RedirectStandardError "$log.err"
    $env:PWD = $saved_pwd
    Wait-Process -Id $proc.Id -Timeout $TimeoutSec -ErrorAction SilentlyContinue
    $timed_out = $false
    if (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        $timed_out = $true
    }
    $elapsed = (Get-Date) - $started
    $quota_after = Get-OllamaQuota
    # A session window that reset mid-task makes the delta meaningless; record it as missing, not as a number.
    $quota_session = $null
    $quota_weekly = $null
    if ($null -ne $quota_before -and $null -ne $quota_after -and $quota_before.session_reset -eq $quota_after.session_reset) {
        $quota_session = [math]::Round($quota_after.session_used - $quota_before.session_used, 2)
        $quota_weekly = [math]::Round($quota_after.weekly_used - $quota_before.weekly_used, 2)
    }
    $verdict = if ($timed_out) { 'TIMEOUT' } elseif ((& $Verify) -eq $true) { 'PASS' } else { 'FAIL' }
    git diff --no-color HEAD 2>$null | Set-Content -Path "$log.diff" -Encoding utf8
    Pop-Location

    # Did the agent stay inside its sandbox? An agent once wrote into the repo the probe ran from [nollama, 2026-09-23].
    $strays = @(git -C $PSScriptRoot status --porcelain -- calc.py tests/ 2>$null)
    if ($strays) {
        Write-Host '  !! the agent wrote OUTSIDE its workdir, into this repo:' -ForegroundColor Red
        $strays | ForEach-Object { Write-Host "     $_" -ForegroundColor Red }
    }
    $stream = Read-Stream -Log $log
    $why = if ($verdict -eq 'FAIL') { Get-FailureKind -Stream $stream -Dir $Dir } else { '' }
    [PSCustomObject]@{ Task = $Label; Verdict = $verdict; Seconds = [int]$elapsed.TotalSeconds; Log = $log; Why = $why; Stream = $stream; QuotaSession = $quota_session; QuotaWeekly = $quota_weekly }
}

# --- run -------------------------------------------------------------------
if (-not $BaseUrl) {
    Write-Host 'ERROR: no endpoint. Pass -BaseUrl or set OLLAMA_5090_URL, e.g. http://<host>:11434' -ForegroundColor Red; exit 2
}
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Host 'ERROR: claude is not on PATH' -ForegroundColor Red; exit 2
}
if (-not $Config) { $Config = "$MainModel+$SubagentModel" }

Write-Host '=== claude agent probe ===' -ForegroundColor Cyan
Write-Host "  server   : $BaseUrl"
Write-Host "  main     : $MainModel"
Write-Host "  subagent : $SubagentModel"
Write-Host "  PASS     : original tests pass afterwards, unaided, within $TimeoutSec s; delegate also needs $SubagentModel in the token use" -ForegroundColor DarkGray
Write-Host '  (criteria printed before the run on purpose)' -ForegroundColor DarkGray
Write-Host ''

New-Fixture -Dir $WorkDir
$settings_file = "$WorkDir.settings.json"
Write-ProbeSettings -Path $settings_file
# Built-in Explore ignores CLAUDE_CODE_SUBAGENT_MODEL; same override as claude-hybrid.ps1.
$agents_file = "$WorkDir.agents.json"
@{
    Explore = @{
        description = 'Read-only codebase search. Use for broad file and code lookups.'
        prompt      = 'You are a read-only search agent. Find what was asked for and report file paths and line numbers. Never edit files.'
        tools       = @('Glob', 'Grep', 'Read', 'Bash')
        model       = 'haiku'
    }
} | ConvertTo-Json -Depth 5 | Set-Content -Path $agents_file -Encoding utf8
$py = (Get-Command python -ErrorAction Stop).Source

$results = @()
$results += Invoke-Task -Dir $WorkDir -Label 'fix' -TimeoutSec $TimeoutSec `
    -Prompt "Run 'python tests/test_calc.py'. Both tests fail. Fix calc.py so they pass, then run the tests again to confirm." `
    -Verify { Test-OriginalTests -Dir $WorkDir }

$results += Invoke-Task -Dir $WorkDir -Label 'feature' -TimeoutSec $TimeoutSec `
    -Prompt "Add a function apply_tax(amount, percent) to calc.py that ADDS that percentage to the amount and rounds to two decimals. Add a test for it in tests/test_calc.py asserting apply_tax(100.0, 25) == 125.0. Then run 'python tests/test_calc.py' and make sure everything passes." `
    -Verify {
        & $py -c "import sys; sys.path.insert(0, r'$WorkDir'); from calc import apply_tax; raise SystemExit(0 if apply_tax(100.0,25)==125.0 else 1)" *> $null
        if ($LASTEXITCODE -ne 0) { return $false }
        if (-not (Select-String -Path (Join-Path $WorkDir 'tests\test_calc.py') -Pattern 'apply_tax' -Quiet)) { return $false }
        & $py (Join-Path $WorkDir 'tests\test_calc.py') *> $null
        if ($LASTEXITCODE -ne 0) { return $false }
        Test-OriginalTests -Dir $WorkDir
    }

$results += Invoke-Task -Dir $WorkDir -Label 'delegate' -TimeoutSec $TimeoutSec `
    -Prompt "Do not edit any file yourself. Use the Agent tool to start a general-purpose subagent and give it this job: run 'python tests/test_calc.py', fix calc.py so both tests pass, and run the tests again to confirm. When it reports back, run the tests yourself to check." `
    -Verify { Test-OriginalTests -Dir $WorkDir }
$billing_dir = "$WorkDir-billing"
$hidden_test = "$WorkDir-hidden.py"
New-BillingFixture -Dir $billing_dir -HiddenTest $hidden_test

$results += Invoke-Task -Dir $billing_dir -Label 'hunt' -TimeoutSec $TimeoutSec `
    -Prompt "The monthly report total is wrong whenever an invoice has lines in a foreign currency; 'python tests/test_report.py' shows it. Find the root cause in the billing package and fix it there, not with a workaround. Run the tests again to confirm." `
    -Verify {
        & $py (Join-Path $billing_dir 'tests\test_report.py') *> $null
        if ($LASTEXITCODE -ne 0) { return $false }
        & $py $hidden_test $billing_dir *> $null
        return ($LASTEXITCODE -eq 0)
    }

$results += Invoke-Task -Dir $billing_dir -Label 'rename' -TimeoutSec $TimeoutSec `
    -Prompt "Rename the function line_total_nok to line_amount_nok everywhere in this project, including the tests, without changing behaviour. The currency test in tests/test_report.py already fails for an unrelated reason that someone else is fixing; leave that alone and do not touch billing/currency.py or billing/rates.py. Run 'python tests/test_invoice.py' to confirm." `
    -Verify {
        $leftovers = @(Get-ChildItem $billing_dir -Recurse -Filter *.py | Select-String -Pattern 'line_total_nok')
        if ($leftovers.Count -gt 0) { return $false }
        & $py (Join-Path $billing_dir 'tests\test_invoice.py') *> $null
        if ($LASTEXITCODE -ne 0) { return $false }
        # Instruction following: the files it was told to leave alone are byte-identical to the fixture.
        $touched = @(git -C $billing_dir diff --name-only HEAD -- billing/currency.py billing/rates.py)
        return ($touched.Count -eq 0)
    }

# delegate only counts if the subagent model actually did work.
$delegate = $results | Where-Object Task -eq 'delegate'
if ($delegate.Verdict -eq 'PASS' -and $MainModel -ne $SubagentModel -and -not $delegate.Stream.Usage.ContainsKey($SubagentModel)) {
    $delegate.Verdict = 'FAIL'
    $delegate.Why = "tests pass, but $SubagentModel shows no token use -- the main model did it itself or routing is wrong"
}

Write-Host ''
foreach ($r in $results) {
    $color = switch ($r.Verdict) { 'PASS' { 'Green' } 'TIMEOUT' { 'Yellow' } default { 'Red' } }
    $usage_text = ($r.Stream.Usage.GetEnumerator() | ForEach-Object { "$($_.Key) in=$($_.Value.in) out=$($_.Value.out)" }) -join '; '
    $quota_text = if ($null -eq $r.QuotaSession) { 'quota=n/a' } else { "quota=+$($r.QuotaSession)%" }
    Write-Host ('  {0,-8} {1,-8} {2,5}s  tools={3} turns={4}  {5}  {6}' -f $r.Task, $r.Verdict, $r.Seconds, $r.Stream.ToolCalls, $r.Stream.Turns, $quota_text, $usage_text) -ForegroundColor $color
    if ($r.Why) { Write-Host "           why: $($r.Why)" -ForegroundColor $color }
}

# Keep transcripts: HOW a model fails is the whole output of this probe.
$keep_dir = Join-Path $PSScriptRoot 'results' 'agent-probe'
New-Item -ItemType Directory -Path $keep_dir -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$safe_config = ($Config -replace '[^A-Za-z0-9._+-]', '_')
$jsonl = Join-Path $PSScriptRoot 'results' "agent-probe-$safe_config.jsonl"
foreach ($r in $results) {
    $dest = Join-Path $keep_dir "$stamp-$safe_config-$($r.Task).log"
    foreach ($suffix in @('', '.err', '.diff')) {
        if (Test-Path "$($r.Log)$suffix") { Copy-Item "$($r.Log)$suffix" "$dest$suffix" -Force }
    }
    [PSCustomObject]@{
        when = $stamp; config = $Config; main = $MainModel; subagent = $SubagentModel; task = $r.Task
        verdict = $r.Verdict; seconds = $r.Seconds; tool_calls = $r.Stream.ToolCalls; tools = $r.Stream.Tools
        turns = $r.Stream.Turns; denials = $r.Stream.Denials; usage = $r.Stream.Usage; why = $r.Why; log = $dest
        ollama_session_pct = $r.QuotaSession; ollama_weekly_pct = $r.QuotaWeekly
    } | ConvertTo-Json -Depth 5 -Compress | Add-Content -Path $jsonl -Encoding utf8
}

$passed = @($results | Where-Object { $_.Verdict -eq 'PASS' }).Count
Write-Host ''
Write-Host "  $passed/$($results.Count) passed for $Config  ->  $jsonl" -ForegroundColor Cyan
if (-not $KeepWorkdir) {
    Remove-Item -Recurse -Force $WorkDir -ErrorAction SilentlyContinue
    Remove-Item -Force $settings_file, $agents_file, $hidden_test -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $billing_dir -ErrorAction SilentlyContinue
}
exit $(if ($passed -eq $results.Count) { 0 } else { 1 })
