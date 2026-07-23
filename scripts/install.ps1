param(
    [string[]]$Components = @("all"),
    [ValidateSet("rtx3090", "minimal")]
    [string]$Profile = "rtx3090",
    [switch]$DownloadModels,
    [switch]$IncludeOptionalModels,
    [switch]$Start
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")

function Has-Component {
    param([string]$Name)
    $lower = $Components | ForEach-Object { $_.ToLowerInvariant() }
    return ($lower -contains "all") -or ($lower -contains $Name.ToLowerInvariant())
}

function Invoke-RepoPython {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 @args
    } else {
        & python @args
    }
}

function Install-Server {
    Write-Host "[server] creating Python venv and installing API server dependencies"
    $venvDir = Join-Path $Root ".venv"
    $venvPy = Join-Path $venvDir "Scripts\python.exe"
    if (!(Test-Path $venvPy)) {
        Invoke-RepoPython -m venv $venvDir
    }
    & $venvPy -m pip install --upgrade pip
    & $venvPy -m pip install -r (Join-Path $Root "requirements.txt")

    $config = Join-Path $Root "config.yaml"
    if (!(Test-Path $config)) {
        Copy-Item (Join-Path $Root "config.example.yaml") $config
        Write-Host "[server] created config.yaml from config.example.yaml"
    }
}

function Install-Ollama {
    param([string]$ProjectRoot)
    Write-Host "[ollama] checking Ollama"
    if (Get-Command ollama -ErrorAction SilentlyContinue) {
        Write-Host "[ollama] already installed"
        return
    }

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "[ollama] installing with winget"
        winget install --id Ollama.Ollama -e --accept-package-agreements --accept-source-agreements
        return
    }

    $installer = Join-Path $env:TEMP "OllamaSetup.exe"
    Write-Host "[ollama] downloading installer"
    Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $installer
    Start-Process -FilePath $installer -Wait
}

function Install-ComfyUI {
    param([string]$ProjectRoot)
    Write-Host "[comfyui] installing ComfyUI"
    $deps = Join-Path $ProjectRoot "deps"
    $comfyDir = Join-Path $deps "ComfyUI"
    New-Item -ItemType Directory -Force $deps | Out-Null

    if (!(Test-Path $comfyDir)) {
        git clone https://github.com/comfyanonymous/ComfyUI.git $comfyDir
    } else {
        git -C $comfyDir pull --ff-only
    }

    $venvDir = Join-Path $comfyDir ".venv"
    $venvPy = Join-Path $venvDir "Scripts\python.exe"
    if (!(Test-Path $venvPy)) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            & py -3 -m venv $venvDir
        } else {
            & python -m venv $venvDir
        }
    }
    & $venvPy -m pip install --upgrade pip
    & $venvPy -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
    & $venvPy -m pip install -r (Join-Path $comfyDir "requirements.txt")
}

Install-Server

$jobs = @()
if (Has-Component "ollama") {
    $jobs += Start-Job -Name "ollama-install" -ArgumentList $Root -ScriptBlock ${function:Install-Ollama}
}
if (Has-Component "comfyui") {
    $jobs += Start-Job -Name "comfyui-install" -ArgumentList $Root -ScriptBlock ${function:Install-ComfyUI}
}

foreach ($job in $jobs) {
    Receive-Job -Job $job -Wait -AutoRemoveJob
}

if ($DownloadModels) {
    $venvPy = Join-Path $Root ".venv\Scripts\python.exe"
    $componentsArg = if ($Components -contains "all") { "all" } else { ($Components -join ",") }
    $argsList = @(
        (Join-Path $Root "scripts\download_models.py"),
        "--profile", $Profile,
        "--components", $componentsArg
    )
    if ($IncludeOptionalModels) {
        $argsList += "--include-optional"
    }
    & $venvPy @argsList
}

if ($Start) {
    & (Join-Path $Root "scripts\start.ps1") -Components $Components
}

Write-Host "Install complete."

