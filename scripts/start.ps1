param(
    [string[]]$Components = @("all"),
    [string]$HostName = "0.0.0.0",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")

function Has-Component {
    param([string]$Name)
    $lower = $Components | ForEach-Object { $_.ToLowerInvariant() }
    return ($lower -contains "all") -or ($lower -contains $Name.ToLowerInvariant())
}

function Test-Url {
    param([string]$Url)
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

$jobs = @()

if (Has-Component "ollama") {
    if (Test-Url "http://127.0.0.1:11434/api/tags") {
        Write-Host "[ollama] already running"
    } else {
        Write-Host "[ollama] starting"
        $jobs += Start-Job -Name "ollama-serve" -ScriptBlock { ollama serve }
    }
}

if (Has-Component "comfyui") {
    if (Test-Url "http://127.0.0.1:8188/system_stats") {
        Write-Host "[comfyui] already running"
    } else {
        Write-Host "[comfyui] starting"
        $comfyDir = Join-Path $Root "deps\ComfyUI"
        $comfyPy = Join-Path $comfyDir ".venv\Scripts\python.exe"
        if (!(Test-Path $comfyPy)) {
            throw "ComfyUI venv not found. Run scripts\install.ps1 -Components comfyui first."
        }
        $jobs += Start-Job -Name "comfyui-serve" -ArgumentList $comfyDir, $comfyPy -ScriptBlock {
            param($Dir, $Python)
            Set-Location $Dir
            & $Python main.py --listen 127.0.0.1 --port 8188
        }
    }
}

try {
    Write-Host "[server] starting OpenAI-compatible API at http://127.0.0.1:$Port"
    $serverPy = Join-Path $Root ".venv\Scripts\python.exe"
    if (!(Test-Path $serverPy)) {
        throw "Server venv not found. Run scripts\install.ps1 first."
    }
    Set-Location $Root
    & $serverPy -m uvicorn openaiserve.app:app --host $HostName --port $Port
} finally {
    foreach ($job in $jobs) {
        Stop-Job -Job $job -ErrorAction SilentlyContinue
        Remove-Job -Job $job -ErrorAction SilentlyContinue
    }
}

