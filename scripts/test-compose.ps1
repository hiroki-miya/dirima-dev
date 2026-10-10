param(
    [string]$EnvFile = '.env',
    [string]$WslDistribution = '',
    [string]$ProjectName = 'dirima-acceptance'
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$envPath = if ([IO.Path]::IsPathRooted($EnvFile)) { $EnvFile } else { Join-Path $repoRoot $EnvFile }
$envPath = (Resolve-Path -LiteralPath $envPath).Path
foreach ($line in [IO.File]::ReadAllLines($envPath)) {
    if ($line -match '^([A-Z][A-Z0-9_]*)=(.*)$') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
    }
}
function Invoke-Compose([string[]]$ComposeArguments) {
    if ($WslDistribution) {
        $linuxRoot = (wsl.exe -d $WslDistribution --exec wslpath -a $repoRoot.Replace('\', '/')).Trim()
        $linuxEnv = (wsl.exe -d $WslDistribution --exec wslpath -a $envPath.Replace('\', '/')).Trim()
        & wsl.exe -d $WslDistribution -u root -- docker compose --project-directory $linuxRoot --env-file $linuxEnv -p $ProjectName @ComposeArguments
    } else {
        & docker compose --project-directory $repoRoot --env-file $envPath -p $ProjectName @ComposeArguments
    }
    if ($LASTEXITCODE -ne 0) { throw 'Compose failed' }
}
Invoke-Compose -ComposeArguments @('up', '--build', '-d')
Invoke-Compose -ComposeArguments @('ps', '-a')
foreach ($endpoint in @('http://127.0.0.1:8000/readyz', 'http://127.0.0.1:8000/workerz')) {
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $result = Invoke-RestMethod -Uri $endpoint -TimeoutSec 2
            if ($result.status -eq 'ready') { $ready = $true; break }
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw "Health check failed: $endpoint" }
}
Push-Location (Join-Path $repoRoot 'frontend')
try {
    & npm.cmd run test:e2e
    if ($LASTEXITCODE -ne 0) { throw 'Browser acceptance failed' }
} finally { Pop-Location }
# Keep the reviewed stack and its volumes available. Stop it explicitly when finished.
