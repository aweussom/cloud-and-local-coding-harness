#requires -Version 7.0
<#
Summarise probe-subagent.ps1 runs per config and task, from the saved rows and subagent exports.

Why a separate pass: two numbers in the live output are not comparable across servers. Ollama reports only the
UNCACHED part of a prompt as input and the rest as cache.read; SGLang reports the whole prompt as input and no cache
[OBSERVED 2026-11-21: qwen history step input=493 cache.read=9292; Inkling cache.read 0 throughout]. So context is
input + cache.read. And Ollama reports reasoning tokens as 0 while the session holds reasoning parts, so thinking is
measured the same way for both: characters of reasoning text / 4 [INFERRED: ~4 chars per token for English].

In: results/subagent-probe-<config>.jsonl rows and the .subN.json exports next to their logs. Out: one table row per
config and task, with medians over runs, and verdicts re-judged with the CURRENT tuning judge where noted.
#>
param([string[]] $Configs = @('inkling', 'qwen'))
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-ExportNumbers {
    # Reduce one subagent export to comparable numbers.
    # In: path to a .subN.json. Out: peak context, reasoning token estimate, compactions, tool calls and errors.
    param([string]$Path)
    $e = Get-Content $Path -Raw | ConvertFrom-Json
    $assistant = @($e.messages | Where-Object { $_.info.role -eq 'assistant' })
    $parts = @($e.messages | ForEach-Object { $_.parts })
    $tools = @($parts | Where-Object { $_.type -eq 'tool' })
    $reasoning_chars = ($parts | Where-Object { $_.type -eq 'reasoning' } | ForEach-Object { "$($_.text)".Length } | Measure-Object -Sum).Sum
    [PSCustomObject]@{
        peak_context = ($assistant | ForEach-Object { $_.info.tokens.input + $_.info.tokens.cache.read } | Measure-Object -Maximum).Maximum
        reasoning_est = [int]($reasoning_chars / 4)
        compactions = @($parts | Where-Object { $_.type -eq 'compaction' }).Count
        tool_calls = $tools.Count
        tool_errors = @($tools | Where-Object { $_.state.status -eq 'error' }).Count
        steps = $assistant.Count
    }
}

function Get-Median {
    # Median of a list of numbers; $null for an empty list.
    param([double[]]$Values)
    if (-not $Values) { return $null }
    $s = @($Values | Sort-Object)
    if ($s.Count % 2) { return $s[[int][math]::Floor($s.Count / 2)] }
    return ($s[$s.Count / 2 - 1] + $s[$s.Count / 2]) / 2
}

$rows = foreach ($config in $Configs) {
    $file = Join-Path $PSScriptRoot 'results' "subagent-probe-$config.jsonl"
    foreach ($r in (Get-Content $file | ConvertFrom-Json)) {
        $verdict = $r.verdict
        # The first tuning judge demanded the literal key; re-judge with the current one (probe-subagent.ps1).
        if ($r.task -eq 'tuning' -and $verdict -eq 'FAIL' -and $r.answer -match '_apply_tuning' -and $r.answer -match 'repetition_penalty_max|model-tuning\.json') { $verdict = 'PASS*' }
        $exports = @(Get-ChildItem ($r.log -replace '\.jsonl$', '.sub*.json') -ErrorAction SilentlyContinue | Select-Object -First 1)
        $n = if ($exports) { Get-ExportNumbers -Path $exports[0].FullName } else { $null }
        [PSCustomObject]@{ config = $config; task = $r.task; verdict = $verdict; seconds = $r.seconds; n = $n }
    }
}

$table = foreach ($g in ($rows | Group-Object config, task)) {
    $items = @($g.Group)
    $ns = @($items | ForEach-Object { $_.n } | Where-Object { $_ })
    [PSCustomObject]@{
        Config = $items[0].config; Task = $items[0].task
        Pass = '{0}/{1}' -f @($items | Where-Object { $_.verdict -like 'PASS*' }).Count, $items.Count
        'Wall s (median)' = Get-Median ($items | ForEach-Object { $_.seconds })
        'Wall s (range)' = '{0}-{1}' -f ($items.seconds | Measure-Object -Minimum).Minimum, ($items.seconds | Measure-Object -Maximum).Maximum
        'Peak ctx (median)' = Get-Median ($ns | ForEach-Object { $_.peak_context })
        'Thinking est (median)' = Get-Median ($ns | ForEach-Object { $_.reasoning_est })
        Compactions = ($ns | ForEach-Object { $_.compactions } | Measure-Object -Sum).Sum
        'Tool err' = '{0}/{1}' -f ($ns | ForEach-Object { $_.tool_errors } | Measure-Object -Sum).Sum, ($ns | ForEach-Object { $_.tool_calls } | Measure-Object -Sum).Sum
    }
}
$table | Sort-Object Task, Config | Format-Table -AutoSize | Out-String -Width 200
