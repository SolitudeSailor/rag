<#
.SYNOPSIS
统一准备项目虚拟环境并运行 setup、test、demo、experiment 或 export。
.EXAMPLE
.\scripts\run_project.ps1 -Task setup
.EXAMPLE
.\scripts\run_project.ps1 -Task export -InputDir outputs\experiment\exp_1
#>
[CmdletBinding()]
param(
    [ValidateSet('setup', 'test', 'demo', 'experiment', 'export')]
    [string]$Task = 'setup',
    [ValidateSet('V1', 'V2', 'V3', 'blind')]
    [string]$Version = 'V3',
    [ValidateRange(1, 64)]
    [int]$Workers = 3,
    [ValidateSet('on', 'off')]
    [string]$TimeInjection = 'on',
    [string]$InputDir,
    [string]$IndexUrl = 'https://pypi.tuna.tsinghua.edu.cn/simple'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$resultCode = 1
$previousEncoding = $OutputEncoding
$previousConsoleEncoding = [Console]::OutputEncoding
Push-Location -LiteralPath $projectRoot
try {
    $OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    [Console]::OutputEncoding = $OutputEncoding
    if (-not (Test-Path -LiteralPath $projectPython -PathType Leaf)) {
        $venvDirectory = Join-Path $projectRoot '.venv'
        if (Test-Path -LiteralPath $venvDirectory) {
            throw '现有 .venv 缺少 Python，请先检查该环境；启动器不会覆盖它。'
        }
        $bootstrapPython = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($null -eq $bootstrapPython) {
            throw '未找到 Python。请先安装 Python 并将其加入 PATH。'
        }
        Write-Host '[环境] 创建项目 .venv。'
        & $bootstrapPython.Source -m venv $venvDirectory
        if ($LASTEXITCODE -ne 0) {
            throw '创建虚拟环境失败。'
        }
    }
    $runnerArguments = @(
        '-X', 'utf8', '-u', (Join-Path $PSScriptRoot 'project_runner.py'),
        '--task', $Task, '--version', $Version, '--workers', "$Workers",
        '--time-injection', $TimeInjection,
        '--index-url', $IndexUrl
    )
    if ($InputDir) {
        $runnerArguments += @('--input-dir', $InputDir)
    }
    & $projectPython @runnerArguments
    $resultCode = $LASTEXITCODE
}
catch {
    Write-Host ('[启动失败] ' + $_.Exception.Message) -ForegroundColor Red
}
finally {
    $OutputEncoding = $previousEncoding
    [Console]::OutputEncoding = $previousConsoleEncoding
    Pop-Location
}
exit $resultCode
