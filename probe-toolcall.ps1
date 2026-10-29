# Phase 2 probe: does a model behind an Anthropic /v1/messages endpoint emit a real tool_use block?
# Usage: .\probe-toolcall.ps1 -Model qwen3.8:27b [-BaseUrl http://100.98.33.88:11434] [-Runs 5]
param(
    [Parameter(Mandatory)] [string] $Model,
    [string] $BaseUrl = 'http://100.98.33.88:11434',
    [int] $Runs = 5
)
$ErrorActionPreference = 'Stop'

$tools = @(
    @{
        name         = 'read_file'
        description  = 'Read a file from disk and return its contents.'
        input_schema = @{
            type       = 'object'
            properties = @{ path = @{ type = 'string'; description = 'Path to the file' } }
            required   = @('path')
        }
    }
)
$body = @{
    model      = $Model
    max_tokens = 2048
    tools      = $tools
    messages   = @(@{ role = 'user'; content = 'What is in the file /etc/hostname? Use the read_file tool.' })
} | ConvertTo-Json -Depth 10
$headers = @{ 'x-api-key' = 'ollama'; 'anthropic-version' = '2023-06-01' }

Write-Host "probe-toolcall.ps1: trying $Runs runs of $Model at $BaseUrl, expecting tool_use read_file with path /etc/hostname"
$passed = 0
for ($run = 1; $run -le $Runs; $run++) {
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    $response = Invoke-RestMethod "$BaseUrl/v1/messages" -Method Post -ContentType 'application/json' -Headers $headers -Body $body -TimeoutSec 600
    $seconds = $stopwatch.Elapsed.TotalSeconds

    $tool_use = $response.content | Where-Object { $_.type -eq 'tool_use' } | Select-Object -First 1
    $block_types = ($response.content | ForEach-Object { $_.type }) -join ','
    if ($null -eq $tool_use) {
        Write-Host ("run {0}: FAIL {1:N1}s stop={2} blocks={3} (no tool_use block)" -f $run, $seconds, $response.stop_reason, $block_types)
        continue
    }
    if ($tool_use.name -ne 'read_file' -or $tool_use.input.path -ne '/etc/hostname') {
        Write-Host ("run {0}: FAIL {1:N1}s wrong call: {2} {3}" -f $run, $seconds, $tool_use.name, ($tool_use.input | ConvertTo-Json -Compress))
        continue
    }
    $passed++
    Write-Host ("run {0}: PASS {1:N1}s stop={2} blocks={3} out_tokens={4}" -f $run, $seconds, $response.stop_reason, $block_types, $response.usage.output_tokens)
}

Write-Host "probe-toolcall.ps1: $Model passed $passed of $Runs"
if ($passed -ne $Runs) { exit 1 }
